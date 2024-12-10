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
import scdlpicker.inventory as _inventory

from seiscomp.datamodel import \
    EventParameters, Event, \
    Origin, OriginReference, \
    FocalMechanism, FocalMechanismReference, \
    Magnitude, Pick, Amplitude, CreationInfo


def loadEvent(query, publicID, full=True):
    """
    Retrieve an event from DB

    Returns either the event instance or None if it could not be loaded.

    If full==False, getObject() is used, which is very fast as it
    doesn't load all children. Children included anyway are
        preferredOriginID
        preferredMagnitudeID
        preferredFocalMechanismID
        creationInfo

    if full==True, loadObject() is used to additionally load the children:
        comment
        description
        originReference
        focalMechanismReference
    """

    load = query.loadObject if full else query.getObject

    obj = load(seiscomp.datamodel.Event.TypeInfo(), publicID)
    obj = seiscomp.datamodel.Event.Cast(obj)

    return obj  # may be None


def loadOrigin(query, publicID, full=True):
    """
    Retrieve an origin from DB

    Returns either the origin instance or None if it could not be loaded.

    If full==False, getObject() is used, which is very fast as it
    doesn't load all children. Children included anyway are
        creationInfo
        quality
        uncertainty

    if full==True, loadObject() is used to additionally load the children:
        arrival
        comment
        magnitude
        stationMagnitude
    """

    load = query.loadObject if full else query.getObject

    obj = load(seiscomp.datamodel.Origin.TypeInfo(), publicID)
    obj = seiscomp.datamodel.Origin.Cast(obj)

    return obj  # may be None


def loadMagnitude(query, publicID, full=True):
    """
    Retrieve a magnitude from DB

    Returns either the magnitude instance or None if it could not be loaded.

    If full==False, getObject() is used, which is very fast as it
    doesn't load all children. Children included anyway are
        creationInfo

    if full==True, loadObject() is used to additionally load the children:
        comment
    """

    load = query.loadObject if full else query.getObject

    obj = load(seiscomp.datamodel.Magnitude.TypeInfo(), publicID)
    obj = seiscomp.datamodel.Magnitude.Cast(obj)

    return obj  # may be None


def loadPicksForTimespan(query, startTime, endTime, withAmplitudes=False, authors=None):
    """
    Load from the database all picks within the given time span. If specified,
    also all amplitudes that reference any of these picks may be returned.
    """

    seiscomp.logging.debug("loading picks for %s ... %s" % (
        _util.time2str(startTime), _util.time2str(endTime)))

    if authors:
        seiscomp.logging.debug("using author whitelist: " + str(", ".join(authors)))

    objects = dict()

    for obj in query.getPicks(startTime, endTime):
        pick = Pick.Cast(obj)
        if pick:
            objects[pick.publicID()] = pick

    objects = _util.filterObjects(objects, authorWhitelist=authors)

    pickCount = len(objects)
    seiscomp.logging.debug("loaded %d picks" % pickCount)

    if withAmplitudes:
        for obj in query.getAmplitudes(startTime, endTime):
            ampl = Amplitude.Cast(obj)
            if ampl:
                if not ampl.pickID():
                    continue
                if ampl.pickID() not in objects:
                    continue

                objects[ampl.publicID()] = ampl

        amplitudeCount = len(objects) - pickCount
        seiscomp.logging.debug("loaded %d amplitudes" % amplitudeCount)

    return objects


def loadPicksForOrigin(query, origin, inventory, allowedAuthorIDs, maxDelta, maxResidual, keepManualPicks=True):
    """
    For a given origin, load matching picks.

    Picks are matched by comparing pick time and predicted P travel time
    and if the times are withing a short time span, they are selected.

    """
    etime = origin.time().value()
    elat = origin.latitude().value()
    elon = origin.longitude().value()
    edep = origin.depth().value()

    # Uses the iasp91 tables by default
    ttt = seiscomp.seismology.TravelTimeTable()

    # Retrieve a dict of station instances from inventory
    station = _inventory.getStations(inventory, etime)

    # Time span is 20 min for teleseismic applications
    timeSpan = seiscomp.core.TimeSpan(20*60.)
    startTime = origin.time().value()
    endTime = startTime + timeSpan
    picks = loadPicksForTimespan(query, startTime, endTime, authors=allowedAuthorIDs)

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


def loadEventOriginPicks(query, eventID):

    # Load event and preferred origin. This is the minimum
    # required info and if it can't be loaded, give up.
    event = loadEvent(query, eventID)
    if event is None:
        raise ValueError("unknown event '" + eventID + "'")

    ep = EventParameters()
    ep.add(event)

    origin = loadOrigin(query, event.preferredOriginID(), full=True)
    ep.add(origin)

    for pick in query.getPicks(origin.publicID()):
        pick = Pick.Cast(pick)
        ep.add(pick)
#   for ampl in query.getAmplitudesForOrigin(origin.publicID()):
#       ampl = Amplitude.Cast(ampl)
#       ep.add(ampl)

    return ep
