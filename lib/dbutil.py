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
import scdlpicker.defaults as _defaults
import scdlpicker.inventory as _inventory


def loadEvent(query, publicID):
    """
    Retrieve event from DB incl. children
   
    Returns either the event instance
    or None if event could not be loaded.

    Uses loadObject() to also load the children.
    """
    tp = seiscomp.datamodel.Event
    t0 = time.time()
    obj = query.loadObject(tp.TypeInfo(), publicID)
    dt = time.time() - t0
    msg =  "query took %.3f sec for event '%s'" % (dt, publicID)
    log = seiscomp.logging.warning if dt > 0.1 else seiscomp.logging.debug
    log(msg)

    obj = tp.Cast(obj)
    if obj:
        if obj.eventDescriptionCount() == 0:
            query.loadEventDescriptions(obj)
    else:
        seiscomp.logging.error("unknown Event '%s'" % publicID)
    return obj


def loadOrigin(query, publicID):
    # load an Origin object from database
    tp = seiscomp.datamodel.Origin
    t0 = time.time()
    obj = query.loadObject(tp.TypeInfo(), publicID)
    dt = time.time() - t0
    msg =  "query took %.3f sec for event '%s'" % (dt, publicID)
    log = seiscomp.logging.warning if dt > 0.1 else seiscomp.logging.debug
    log(msg)

    obj = tp.Cast(obj)
    if obj is None:
        seiscomp.logging.error("unknown Origin '%s'" % publicID)
    return obj


def loadOriginWithoutArrivals(query, orid, strip=False):
    """
    Retrieve origin from DB *without* children
    
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


def loadPicksForTimespan(query, startTime, endTime, allowedAuthorIDs, withAmplitudes=False):
    """
    Load from the database all picks within the given time span. If specified,
    also all amplitudes that reference any of these picks may be returned.
    """

    seiscomp.logging.debug("loading picks for %s ... %s" % (_util.time2str(startTime), _util.time2str(endTime)))
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



def loadPicksForOrigin(origin, inventory, allowedAuthorIDs, maxDelta, query):
    etime = origin.time().value()
    elat = origin.latitude().value()
    elon = origin.longitude().value()
    edep = origin.depth().value()

    # Clear all arrivals
    while origin.arrivalCount():
        origin.removeArrival(0)

    # Uses the iasp91 tables by default
    ttt = seiscomp.seismology.TravelTimeTable()

    # Retrieve a dict of station instances from inventory
    station = _inventory.getStations(inventory, etime)

    # Time span is 20 min for teleseismic applications
    startTime = origin.time().value()
    endTime = startTime + seiscomp.core.TimeSpan(1200.)
    picks = _dbutil.loadPicksForTimespan(query, startTime, endTime, allowedAuthorIDs)


    # At this point there are many picks we are not interested in because
    # we searched globally for a large time window. We need to focus on the
    # interesting picks, now based on theoretical travel times.

    interesting_picks = dict()
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

        if not -4*_defaults.maxResidual < dt < 4*_defaults.maxResidual:
            continue

        interesting_picks[pickID] = pick

    picks = interesting_picks

    # We can have duplicate DL picks for any stream (nslc) but we
    # only want one pick per nslc.
    picks_per_nslc = dict()
    for pickID in picks:
        pick = picks[pickID]
        nslc = _util.nslc(pick)
        if nslc not in picks_per_nslc:
            picks_per_nslc[nslc] = []
        picks_per_nslc[nslc].append(pick)

    result = []
    for nslc in picks_per_nslc:
        # the first pick per nslc # TODO: review
        sorted_picks = sorted(
            picks_per_nslc[nslc],
            key=lambda p: p.creationInfo().creationTime())
        pick = sorted_picks[0]
        pickID = pick.publicID()

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

        # initially we grab more picks than within the final
        # residual range and trim the residuals later.
        if not -2*_defaults.maxResidual < dt < 2*_defaults.maxResidual:
            print(pickID, "---", dt)
            continue

        result.append(pick)

        phase = seiscomp.datamodel.Phase()
        phase.setCode("P")
        arr = seiscomp.datamodel.Arrival()
        arr.setPhase(phase)
        arr.setPickID(pickID)
        arr.setTimeUsed(delta <= _defaults.maxDelta)
        arr.setWeight(1.)
        origin.add(arr)
        print(pickID, "+++", dt)

    for arr in _util.ArrivalIterator(origin):
        pickID = arr.pickID()
        if not seiscomp.datamodel.Pick.Find(pickID):
            seiscomp.logging.warning("Pick '"+pickID+"' NOT FOUND")

    return origin, result
