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


def loadOrigin(query, orid, strip=False):
    """
    Retrieve origin from DB without children
    
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


def loadPicksForTimespan(query, startTime, endTime, withAmplitudes=False):
    """
    Load from the database all picks within the given time span. If specified,
    also all amplitudes that reference any of these picks may be returned.
    """

    objects = dict()
    for obj in query.getPicks(startTime, endTime):
        pick = seiscomp.datamodel.Pick.Cast(obj)
        if pick:
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
