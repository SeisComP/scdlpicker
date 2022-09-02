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


import seiscomp.datamodel
import seiscomp.logging
import scdlpicker.util

def loadEvent(query, evid):
    """
    Retrieve event from DB incl. children
   
    Returns either the event instance
    or None if event could not be loaded.

    Uses loadObject() to also load the children.
    """
    event = query.loadObject(seiscomp.datamodel.Event.TypeInfo(), evid)
    event = seiscomp.datamodel.Event.Cast(event)
    if event:
        if event.eventDescriptionCount() == 0:
            query.loadEventDescriptions(event)
    return event


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

    objects = dict()
    for obj in query.getPicks(startTime, endTime):
        pick = seiscomp.datamodel.Pick.Cast(obj)
        if pick:
            if scdlpicker.util.authorOf(pick) not in allowedAuthorIDs:
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



def loadPicksForOrigin(origin, inventory, allowedAuthorIDs, query):
    etime = origin.time().value()
    elat = origin.latitude().value()
    elon = origin.longitude().value()
    edep = origin.depth().value()

    # clear all arrivals
    while origin.arrivalCount():
        origin.removeArrival(0)

    # uses the iasp91 tables by default
    ttt = seiscomp.seismology.TravelTimeTable()

    # retrieve a dict of station instances from inventory
    station = scdlpicker.inventory.getStations(inventory, etime)

    startTime = origin.time().value()
    endTime = startTime + seiscomp.core.TimeSpan(1200.)
    picks = scdlpicker.dbutil.loadPicksForTimespan(query, startTime, endTime, allowedAuthorIDs)

    # We can have duplicate DL picks for any stream (nslc) but we
    # only want one pick per nslc.
    picks_per_nslc = dict()
    for pickID in picks:
        pick = picks[pickID]
        nslc = scdlpicker.util.nslc(pick)
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

        n,s,l,c = nslc
        try:
            sta = station[n,s]
        except KeyError as e:
            seiscomp.logging.error(str(e))
            continue

        slat = sta.latitude()
        slon = sta.longitude()

        delta, az, baz = seiscomp.math.delazi_wgs84(elat, elon, slat, slon)

        ttimes = ttt.compute(0, 0, edep, 0, delta, 0, 0)
        ptime = ttimes[0]

        theo = etime + seiscomp.core.TimeSpan(ptime.time)
        dt = float(pick.time().value() - theo)

        maxDelta = scdlpicker.defaults.maxDelta
        maxResidual = scdlpicker.defaults.maxResidual

        # initially we grab more picks than within the final
        # residual range and trim the residuals later.
        if -2*maxResidual < dt < 2*maxResidual:
            result.append(pick)

            phase = seiscomp.datamodel.Phase()
            phase.setCode("P")
            arr = seiscomp.datamodel.Arrival()
            arr.setPhase(phase)
            arr.setPickID(pickID)
            arr.setTimeUsed(delta <= maxDelta)
            arr.setWeight(1.)
            origin.add(arr)
            print(pickID, "+++", dt)
        else:
            print(pickID, "---", dt)

    for arr in scdlpicker.util.ArrivalIterator(origin):
        pickID = arr.pickID()
        if not seiscomp.datamodel.Pick.Find(pickID):
            seiscomp.logging.warning("Pick '"+pickID+"' NOT FOUND")

    return origin, result
