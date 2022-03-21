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


import seiscomp.core
import seiscomp.datamodel
import seiscomp.logging
import seiscomp.io


def nslc(obj):
    """
    Convenience function to retrieve network, station, location and
    channel codes from a waveformID object and return them as tuple
    """
    if isinstance(obj, seiscomp.datamodel.WaveformStreamID) or \
       isinstance(obj, seiscomp.core.Record):
        n = obj.networkCode()
        s = obj.stationCode()
        l = obj.locationCode()
        c = obj.channelCode()
    else:
        return nslc(obj.waveformID())
    return n,s,l,c


def uncertainty(quantity):
    try:
        err = 0.5*(quantity.lowerUncertainty()+quantity.upperUncertainty())
    except:
        try:
            err = quantity.uncertainty()
        except:
            err = None
    return err


def isotimestamp(time, decimals=3):
    """
    Convert a seiscomp.core.Time to a timestamp YYYY-MM-DDTHH:MM:SS.sssZ
    """
    return time.toString("%Y-%m-%dT%H:%M:%S.%f000000")[:20+decimals].strip(".")+"Z"


def time2str(time, decimals=1):
    """
    Convert a seiscomp.core.Time to a string YYYY-MM-DD HH:MM:SS.s
    """
    return time.toString("%Y-%m-%d %H:%M:%S.%f000000")[:20+decimals]



def RecordIterator(recordstream, showprogress=False):
        count = 0
        # It would be desirable to not need to unpack the records.
        # Just pass around the raw records.
        inp = seiscomp.io.RecordInput(
                    recordstream,
                    seiscomp.core.Array.INT,
                    seiscomp.core.Record.SAVE_RAW)
        while True:
            try:
                rec = inp.next()
            except Exception as exc:
                seiscomp.logging.error(str(exc))
                rec = None

            if not rec:
                break
            if showprogress:
                count += 1
                sys.stderr.write("%-20s %6d\r" % (rec.streamID(), count))
            yield rec


def ArrivalIterator(origin):
    for i in range(origin.arrivalCount()):
        yield origin.arrival(i)


def status(obj):
    try:
        stat = seiscomp.datamodel.EEvaluationStatusNames.name(obj.evaluationStatus())
    except:
        stat = "NULL"
    try:
        mode = seiscomp.datamodel.EEvaluationModeNames.name(obj.evaluationMode())
    except:
        mode = "NULL"
    return "%s / %s" % (mode, stat)


def valid(event):
    """
    Check if the event is valid, i.e. it is not None and it has an
    'allowed' type.
    """
    if event is None:
        return False
    try:
        typename = seiscomp.datamodel.EEventTypeNames.name(event.type())
        if typename.lower() in [
                "not existing", "not locatable", "other", "not reported"]:
            return False
    except:
        pass
    return True


def summarize(obj, withPicks=False):
    print("Origin %s" % obj.publicID())
    print("  Status      %s" % status(obj))

    tstr = time2str(obj.time().value())
    print("  Time       %s" % tstr)
    lat = obj.latitude().value()
    lon = obj.longitude().value()
    print("  Latitude   %+8.3f" % lat)
    print("  Longitude  %+8.3f" % lon)
    dep = obj.depth()
    val = dep.value()
    if uncertainty(dep):
        print("  Depth      %4.4g km" % val)
    else:
        print("  Depth      %4.4g km fixed" % val)

    countAll = 0
    countUsed = 0
    for arr in ArrivalIterator(obj):
        if arr.weight() > 0.5:
            countUsed += 1
        countAll += 1

    print("  Arr used  %d" % countUsed)
    print("  Arr all   %d" % countAll)
    print("  Pha count %d" % obj.quality().usedPhaseCount())

    # FIXME: usedStationCount and standardError are currently sometimes
    #        adopted from the seeding origin
    # TODO:  ensure usedStationCount and standardError are always computed
    try:
        print("  Sta count %d" % obj.quality().usedStationCount())
    except ValueError:
        pass
    try:
        print("  RMS        %.2f" % obj.quality().standardError())
    except ValueError:
        pass

    if withPicks:
        time_picks = []
        for arr in ArrivalIterator(obj):
            pickID = arr.pickID()
            pick = seiscomp.datamodel.Pick.Find(pickID)
            if not pick:
                seiscomp.logging.warning("Pick '"+pickID+"' NOT FOUND")
                continue
            time_picks.append((pick.time().value(), pick))
        for t,pick in sorted(time_picks):
            print("  %s" % pick.publicID())


def dumpOriginXML(origin, xmlFileName):
    seiscomp.logging.debug("dumping origin '%s' to XML file '%s'"
        % (origin.publicID(), xmlFileName))
    ep = seiscomp.datamodel.EventParameters()
    ep.add(origin)
    for arr in ArrivalIterator(origin):
        pickID = arr.pickID()
        pick = seiscomp.datamodel.Pick.Find(pickID)
        if not pick:
            seiscomp.logging.warning("Pick '"+pickID+"' NOT FOUND")
        ep.add(pick)
    ar = seiscomp.io.XMLArchive()
    ar.setFormattedOutput(True)
    ar.create(xmlFileName)
    ar.writeObject(ep)
    ar.close()
    return True
