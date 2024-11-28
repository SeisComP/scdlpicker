#!/usr/bin/env python
# -*- coding: utf-8 -*-
###########################################################################
# Copyright (C) GFZ Potsdam                                               #
# All rights reserved.                                                    #
#                                                                         #
# Author: Joachim Saul (saul@gfz-potsdam.de)                              #
#                                                                         #
# GNU Affero General Public License Usage                                 #
# This file may be used under the terms of the GNU Affero                 #
# Public License version 3.0 as published by the Free Software Foundation #
# and appearing in the file LICENSE included in the packaging of this     #
# file. Please review the following information to ensure the GNU Affero  #
# Public License version 3.0 requirements will be met:                    #
# https://www.gnu.org/licenses/agpl-3.0.html.                             #
###########################################################################


import time
import seiscomp.datamodel
import seiscomp.logging
import scdlpicker.util as _util
import scdlpicker.dbutil as _dbutil
import scdlpicker.inventory as _inventory


def loadEvent(query, publicID):
    """
    Retrieve event from DB incl. children

    Returns either the event instance
    or None if event could not be loaded.

    Uses loadObject() to also load the children.
    """
    obj = query.loadObject(seiscomp.datamodel.Event.TypeInfo(), eventID)
    event = seiscomp.datamodel.Event.Cast(obj)
    if event:
        if event.eventDescriptionCount() == 0:
            query.loadEventDescriptions(event)
        return event


def loadBareOrigin(query, orid):
    """
    Retrieve 'bare' origin from DB, i.e. without children

    Returns either the origin instance
    or None if origin could not be loaded.
    """

    # Remark: An Origin can be loaded using loadObject() and
    # getObject(). The difference is that getObject() doesn't
    # load the arrivals hence is a *lot* faster.
    # origin = query.loadObject(seiscomp.datamodel.Origin.TypeInfo(), orid)
    origin = query.getObject(seiscomp.datamodel.Origin.TypeInfo(), orid)
    origin = seiscomp.datamodel.Origin.Cast(origin)
    return origin


def loadMagnitude(query, orid):
    """
    Retrieve magnitude from DB without children

    Returns either the Magnitude instance
    or None if Magnitude could not be loaded.
    """
    obj = query.getObject(seiscomp.datamodel.Magnitude.TypeInfo(), orid)
    return seiscomp.datamodel.Magnitude.Cast(obj)


def loadPicksForTimespan(query, startTime, endTime,
                         allowedAuthorIDs, withAmplitudes=False):
    """
    Load from the database all picks within the given time span. If specified,
    also all amplitudes that reference any of these picks may be returned.
    """

    seiscomp.logging.debug("loading picks for %s ... %s" % (
        _util.time2str(startTime), _util.time2str(endTime)))
    objects = dict()
    for obj in query.getPicks(startTime, endTime):
        pick = seiscomp.datamodel.Pick.Cast(obj)
        if pick:
            if _util.authorOf(pick) not in allowedAuthorIDs:
                continue
            objects[pick.publicID()] = pick

    pickCount = len(objects)
    seiscomp.logging.debug("loaded %d picks" % pickCount)

    if not withAmplitudes:
        return objects

    for obj in query.getAmplitudes(startTime, endTime):
        ampl = seiscomp.datamodel.Amplitude.Cast(obj)
        if ampl:
            if not ampl.pickID():
                continue
            if ampl.pickID() not in objects:
                continue
            objects[ampl.publicID()] = ampl

    amplitudeCount = len(objects) - pickCount
    seiscomp.logging.debug("loaded %d amplitudes" % amplitudeCount)

    return objects


def loadPicksForOrigin(origin, inventory, allowedAuthorIDs, maxDelta, maxResidual, query, keepManualPicks=True):
    etime = origin.time().value()
    elat = origin.latitude().value()
    elon = origin.longitude().value()
    edep = origin.depth().value()

    # Uses the iasp91 tables by default
    ttt = seiscomp.seismology.TravelTimeTable()

    # Retrieve a dict of station instances from inventory
    station = _inventory.getStations(inventory, etime)

    # Time span is 20 min for teleseismic applications
    startTime = origin.time().value()
    endTime = startTime + seiscomp.core.TimeSpan(1200.)
    picks = _dbutil.loadPicksForTimespan(
        query, startTime, endTime, allowedAuthorIDs)

    # At this point there are many picks we are not interested in because
    # we searched globally for a large time window. We need to focus on the
    # interesting picks, now based on theoretical travel times.

    picks_of_interest = dict()
    for pickID in picks:
        pick = picks[pickID]
        nslc = _util.nslc(pick)
        n, s, l, c = nslc
        try:
            sta = station[n, s]
        except KeyError as e:
            seiscomp.logging.error(str(e))
            continue

        slat = sta.latitude()
        slon = sta.longitude()

        delta, az, baz = seiscomp.math.delazi_wgs84(elat, elon, slat, slon)

        if delta > maxDelta:
            continue

        ttimes = ttt.compute(0, 0, edep, 0, delta, 0, 0)
        ptime = ttimes[0]

        theo = etime + seiscomp.core.TimeSpan(ptime.time)
        dt = float(pick.time().value() - theo)

        if not -4*maxResidual < dt < 4*maxResidual:
            continue

        picks_of_interest[pickID] = pick

    picks = picks_of_interest

    # We can have duplicate DL picks for any stream (nslc) but we
    # only want one pick per nslc.
    picks_per_nslc = dict()
    for pickID in picks:
        pick = picks[pickID]
        nslc = _util.nslc(pick)
        if nslc not in picks_per_nslc:
            picks_per_nslc[nslc] = []
        picks_per_nslc[nslc].append(pick)

    for nslc in picks_per_nslc:
        picks_per_nslc[nslc] = sorted(
            picks_per_nslc[nslc],
            key=lambda p: p.creationInfo().creationTime())

    _util.clearAllArrivals(origin)
    query.loadArrivals(origin)
    associated_picks = dict()
    for pick in query.getPicks(origin.publicID()):
        pick = seiscomp.datamodel.Pick.Cast(pick)
        if not pick:
            continue
        associated_picks[pick.publicID()] = pick

    if keepManualPicks:
        _util.clearAutomaticArrivals(origin)
        manual_picks = dict()
        # This list contains stream ID and phase code of manual picks.
        # This is used to block DL picks from streams for which we have a
        # manual pick for the same phase type.
        stream_phase_list = []
        for arr in _util.ArrivalIterator(origin):
            pickID = arr.pickID()
            pick = associated_picks[pickID]
            manual_picks[pickID] = pick
            stream_phase_list.append( (pick.waveformID(), arr.phase().code()) )

        # Find the right picks and associate them to the origin
        picks = list(manual_picks.values())
    else:
        _util.clearAllArrivals(origin)
        picks = list()


    for nslc in picks_per_nslc:
        # Take the first-created pick per nslc
        # TODO: review
        pick = picks_per_nslc[nslc][0]
        pickID = pick.publicID()

        # If we already have a P pick for that stream...
        if (pick.waveformID(), "P") in stream_phase_list:
            continue

        n, s, l, c = nslc
        try:
            sta = station[n, s]
        except KeyError as e:
            seiscomp.logging.error(str(e))
            continue

        slat = sta.latitude()
        slon = sta.longitude()

        delta, az, baz = seiscomp.math.delazi_wgs84(elat, elon, slat, slon)

        if delta > maxDelta:
            continue

        ttimes = ttt.compute(0, 0, edep, 0, delta, 0, 0)
        ptime = ttimes[0]

        theo = etime + seiscomp.core.TimeSpan(ptime.time)
        dt = float(pick.time().value() - theo)

        # Initially we grab more picks than within the final
        # residual range and trim the residuals later.
        if not -2*maxResidual < dt < 2*maxResidual:
            print(pickID, "---", dt)
            continue

        picks.append(pick)

        phase = seiscomp.datamodel.Phase()
        phase.setCode("P")
        arr = seiscomp.datamodel.Arrival()
        arr.setPhase(phase)
        arr.setPickID(pickID)
        arr.setTimeUsed(delta <= maxDelta)
        arr.setWeight(1.)
        origin.add(arr)
        print(pickID, "+++", dt)

    for arr in _util.ArrivalIterator(origin):
        pickID = arr.pickID()
        if not seiscomp.datamodel.Pick.Find(pickID):
            seiscomp.logging.warning("Pick '"+pickID+"' NOT FOUND")

    return origin, picks


def loadCompleteEvent(
        query, eventID,
        preferredOriginID=None,
        preferredMagnitudeID=None,
        preferredFocalMechanismID=None,
        comments=False, allmagnitudes=False,
        withPicks=False, preferred=False):
    """
    Load a "complete" event from the database via the specified
    query.

    "Complete" here means Event with preferred Origin, Magnitude,
    FocalMechanism, Picks, Amplitudes, etc. but not all Origins etc.
    Only what filly represents all the preferred children.

    It is possible to override the preferredOriginID,
    preferredMagnitudeID and preferredFocalMechanismID.

    Things to do:
    * load event
    * load preferred origin without arrivals
    * load at least the preferred magnitude if available, all
      magnitudes if requested
    * load focal mechanism incl. moment tensor depending on availability,
      incl. Mw from derived origin
    """

    ep = EventParameters()
    # Load event and preferred origin. This is the minimum
    # required info and if it can't be loaded, give up.
    event = loadEvent(query, eventID)
    if event is None:
        msg = "unknown event '" + eventID + "'"
        # seiscomp.logging.error(msg)
        raise ValueError(msg)

    # We have the possibility to override the preferredOriginID etc.
    # but need to do this at the beginning.
    if preferredOriginID:
        event.setPreferredOriginID(preferredOriginID)
    if preferredMagnitudeID:
        event.setPreferredMagnitudeID(preferredMagnitudeID)
    if preferredFocalMechanismID:
        event.setPreferredFocalMechanismID(preferredFocalMechanismID)

    origins = dict()
    focalMechanisms = dict()

    preferredOrigin = None
    preferredFocalMechanism = None

    # Load all origins that are children of EventParameters. Currently
    # this does not load derived origins because for these there is no
    # originReference created, which is probably a bug. FIXME!
    for origin in query.getOrigins(eventID):
        origin = Origin.Cast(origin)
        # The origin is bare minimum without children.
        # No arrivals, magnitudes, comments... will load those later.
        if not origin:
            continue
        origins[origin.publicID()] = origin

    # Load all focal mechanisms and then load moment tensor children
    for focalMechanism in query.getFocalMechanismsDescending(eventID):
        focalMechanism = FocalMechanism.Cast(focalMechanism)
        if not focalMechanism:
            continue
        focalMechanisms[focalMechanism.publicID()] = focalMechanism
    for focalMechanismID in focalMechanisms:
        focalMechanism = focalMechanisms[focalMechanismID]
        query.loadMomentTensors(focalMechanism)
        # query.loadComments(focalMechanism)


    # Load triggering and derived origins for all focal mechanisms and moment tensors
    #
    # A derived origin may act as a triggering origin of another focal mechanisms.
    for focalMechanismID in focalMechanisms:
        focalMechanism = focalMechanisms[focalMechanismID]

        for i in range(focalMechanism.momentTensorCount()):
            momentTensor = focalMechanism.momentTensor(i)
            if momentTensor.derivedOriginID():
                derivedOriginID = momentTensor.derivedOriginID()
                # assert derivedOriginID not in origins

                if derivedOriginID not in origins:
                    derivedOrigin = loadBareOrigin(query, derivedOriginID)
                    if derivedOrigin is None:
                        seiscomp.logging.warning("%s: failed to load derived origin %s" % (eventID, derivedOriginID))
                    else:
                        stripOrigin(derivedOrigin)
                        origins[derivedOriginID] = derivedOrigin

        triggeringOriginID = focalMechanism.triggeringOriginID()
        if triggeringOriginID not in origins:
            # Actually not unusual. Happens if a derived origin is used as
            # triggering origin, as for the derived origins there is no
            # OriginReference. So rather than a warning we only issue a
            # debug message.
            #
            seiscomp.logging.debug("%s: triggering origin %s not in origins" % (eventID, triggeringOriginID))
            triggeringOrigin = loadBareOrigin(query, triggeringOriginID)
            if triggeringOrigin is None:
                seiscomp.logging.warning("%s: failed to load triggering origin %s" % (eventID, triggeringOriginID))
            else:
                stripOrigin(triggeringOrigin)
                origins[triggeringOriginID] = triggeringOrigin

    # Load arrivals, comments, magnitudes into origins
    for originID in origins:
        origin = origins[originID]

        if withPicks:
            query.loadArrivals(origin)
        if comments:
            query.loadComments(origin)
        query.loadMagnitudes(origin)

    if event.preferredOriginID():
        preferredOriginID = event.preferredOriginID()
    else:
        preferredOriginID = None

    if preferredOrigin is None:
        if preferredOriginID and preferredOriginID in origins:
            preferredOrigin = origins[preferredOriginID]
        if preferredOrigin is None:
            raise RuntimeError(
                "preferred origin '" + preferredOriginID + "' not found")

    # Load all magnitudes for all loaded origins
    magnitudes = dict()
    if allmagnitudes:
        for originID in origins:
            origin = origins[originID]
            for i in range(origin.magnitudeCount()):
                magnitude = origin.magnitude(i)
                magnitudes[magnitude.publicID()] = magnitude
    # TODO station magnitudes

    preferredMagnitude = None
    if event.preferredMagnitudeID():
        if event.preferredMagnitudeID() in magnitudes:
            preferredMagnitude = magnitudes[event.preferredMagnitudeID()]
#       for magnitudeID in magnitudes:
#           magnitude = magnitudes[magnitudeID]
#           if magnitude.publicID() == event.preferredMagnitudeID():
#               preferredMagnitude = magnitude
        if not preferredMagnitude:
            seiscomp.logging.warning("%s: magnitude %s not found" % (eventID, event.preferredMagnitudeID()))
            # Try to load from memory
            preferredMagnitude = Magnitude.Find(event.preferredMagnitudeID())
        if not preferredMagnitude:
            seiscomp.logging.warning("%s: magnitude %s not found in memory either" % (eventID, event.preferredMagnitudeID()))
            # Load it from database
            preferredMagnitude = loadMagnitude(query, event.preferredMagnitudeID())
        if not preferredMagnitude:
            seiscomp.logging.warning("%s: magnitude %s not found in database either" % (eventID, event.preferredMagnitudeID()))

    # Load focal mechanism, moment tensor, moment magnitude and related origins
    momentTensor = momentMagnitude = derivedOrigin = triggeringOrigin = None
#   preferredFocalMechanism = loadFocalMechanism(
#       query, event.preferredFocalMechanismID())
    if event.preferredFocalMechanismID():
        preferredFocalMechanism = focalMechanisms[event.preferredFocalMechanismID()]
#   if preferredFocalMechanism:
        for i in range(preferredFocalMechanism.momentTensorCount()):
            momentTensor = preferredFocalMechanism.momentTensor(i)
            stripMomentTensor(momentTensor)

        if preferredFocalMechanism.triggeringOriginID():
            if event.preferredOriginID() == preferredFocalMechanism.triggeringOriginID():
                triggeringOrigin = preferredOrigin
            else:
                if preferredFocalMechanism.triggeringOriginID() in origins:
                    triggeringOrigin = origins[preferredFocalMechanism.triggeringOriginID()]
                else:
                    triggeringOrigin = None

                if not triggeringOrigin:
                    seiscomp.logging.warning("triggering origin %s not in origins" % preferredFocalMechanism.triggeringOriginID())
                if not triggeringOrigin:
                    triggeringOrigin = loadBareOrigin(
                        query, preferredFocalMechanism.triggeringOriginID(), strip=True)
                if not triggeringOrigin:
                    seiscomp.logging.warning("triggering origin %s not in database either" % preferredFocalMechanism.triggeringOriginID())
                    raise RuntimeError()

            # TODO: Strip triggering origin if it is not the preferred origin

        if preferredFocalMechanism.momentTensorCount() > 0:
            # FIXME What if there is more than one MT?
            momentTensor = preferredFocalMechanism.momentTensor(0)
            if momentTensor.derivedOriginID():
                if momentTensor.derivedOriginID() not in origins:
                    seiscomp.logging.warning("momentTensor.derivedOriginID() not in origins")
                    derivedOrigin = loadBareOrigin(
                        query, momentTensor.derivedOriginID(), strip=True)
                    origins[momentTensor.derivedOriginID()] = derivedOrigin
            if momentTensor.momentMagnitudeID():
                if momentTensor.momentMagnitudeID() == \
                        event.preferredMagnitudeID():
                    momentMagnitude = preferredMagnitude
                else:
                    momentMagnitude = loadMagnitude(
                        query, momentTensor.momentMagnitudeID())

        # Take care of FocalMechanism and related references
#       if derivedOrigin:
#           event.add(OriginReference(derivedOrigin.publicID()))
#       if triggeringOrigin:
#           if event.preferredOriginID() != triggeringOrigin.publicID():
#               event.add(OriginReference(triggeringOrigin.publicID()))
        while (event.focalMechanismReferenceCount() > 0):
            event.removeFocalMechanismReference(0)
        if preferredFocalMechanism:
            event.add(FocalMechanismReference(preferredFocalMechanism.publicID()))

    # Strip creation info
    includeFullCreationInfo = True
    if not includeFullCreationInfo:
        stripAuthorInfo(event)

#       if preferredFocalMechanism:
#           stripCreationInfo(preferredFocalMechanism)
#           for i in range(preferredFocalMechanism.momentTensorCount()):
#               stripCreationInfo(preferredFocalMechanism.momentTensor(i))
        for org in [ preferredOrigin, triggeringOrigin, derivedOrigin ]:
            if org is not None:
                stripAuthorInfo(org)
                for i in range(org.magnitudeCount()):
                    stripAuthorInfo(org.magnitude(i))

    picks = dict()
    ampls = dict()
    if withPicks:
        for originID in origins:
            for pick in query.getPicks(originID):
                pick = Pick.Cast(pick)
                if pick.publicID() not in picks:
                    picks[pick.publicID()] = pick
            for ampl in query.getAmplitudesForOrigin(origin.publicID()):
                ampl = Amplitude.Cast(ampl)
                if ampl.publicID() not in ampls:
                    ampls[ampl.publicID()] = ampl

    # Populate EventParameters instance
    ep.add(event)
#   if preferredMagnitude and preferredMagnitude is not momentMagnitude:
#       preferredOrigin.add(preferredMagnitude)

    while (event.originReferenceCount() > 0):
        event.removeOriginReference(0)
    for originID in origins:
        event.add(OriginReference(originID))
        ep.add(origins[originID])

    if preferredFocalMechanism:
#       if triggeringOrigin:
#           if triggeringOrigin is not preferredOrigin:
#               ep.add(triggeringOrigin)
        if derivedOrigin:
            if momentMagnitude:
                derivedOrigin.add(momentMagnitude)
#           ep.add(derivedOrigin)
        ep.add(preferredFocalMechanism)

    for pickID in picks:
        ep.add(picks[pickID])
    for amplID in ampls:
        ep.add(ampls[amplID])

    if not comments:
        scstuff.util.recursivelyRemoveComments(ep)

    return ep
