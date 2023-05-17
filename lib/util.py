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
import math
import sys



def arrivalCount(org, minArrivalWeight=0.5):
    count = 0
    for i in range(org.arrivalCount()):
        arr = org.arrival(i)
        if arr.weight() >= minArrivalWeight:
            count += 1
    return count


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


def authorOf(obj):
    try:
        return obj.creationInfo().author()
    except:
        return None


def hasFixedDepth(origin):
    """
    If the depth of the given origin is fixed, return True,
    otherwise return False.
    """
    if uncertainty(origin.depth()) in [ None, 0.0 ]:
        return True
    return False


def sumOfLargestGaps(azi, n=2):
    """
    From an unsorted list of azimuth values, determine the
    largest n gaps and return their sum.
    """

    gap = []
    aziCount = len(azi)
    if aziCount<2:
        return 360.
    azi = sorted(azi)

    for i in range(1, aziCount):
        gap.append(azi[i]-azi[i-1])
    gap.append(azi[0]-azi[aziCount-1]+360)
    gap = sorted(gap, reverse=True)
    return sum(gap[0:n])


def computeTGap(origin, maxDelta=180, minWeight=0.5):
    """
    Compute the sum of the largest two gaps. Unlike for the well-known
    secondary azimuthal gap, the TGap does not depend on the number of
    stations that separate two gaps. Also the two largest gaps don't
    have to be adjacent.
    """

    azi = []
    arrivalCount = origin.arrivalCount()
    for i in range(arrivalCount):
        arr = origin.arrival(i)
        try:
            azimuth = arr.azimuth()
            weight  = arr.weight()
            delta   = arr.distance()
        except:
            continue
        if weight > minWeight and delta < maxDelta:
            azimuth = math.fmod(azimuth, 360.)
            if azimuth < 0:
                azimuth += 360.
            azi.append(azimuth)

    return sumOfLargestGaps(azi, n=2)


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

    print("  Arr count %d" % obj.arrivalCount())
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

    def pick_time(pick):
        return pick.time().value()

    if withPicks:
        picks = []
        for arr in ArrivalIterator(obj):
            pickID = arr.pickID()
            pick = seiscomp.datamodel.Pick.Find(pickID)
            if not pick:
                seiscomp.logging.warning("Pick '"+pickID+"' NOT FOUND")
                continue
            picks.append(pick)
        for pick in sorted(picks, key=pick_time):
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
    origin.detach()
    return True


def statusFlag(obj):
    """
    If the object is 'manual', return 'M' otherwise 'A'.
    """
    try:
        if obj.evaluationMode() == seiscomp.datamodel.MANUAL:
            return "M"
    except:
        pass
    return "A"


def manual(obj):
    return statusFlag(obj) == 'M'


def qualified(origin):
    # Check whether an origin meets certain criteria.
    #
    # This is work in progress and currently very specific to
    # the global monitoring at GFZ. In other contexts this test
    # may have to be adapted or skipped.

    if manual(origin):
        return True

    if origin.arrivalCount() > 0:
        # Ensure sufficient azimuthal coverage. 
        TGap = computeTGap(origin, maxDelta=90)
        if TGap > 270:
            seiscomp.logging.debug("Origin %s TGap=%.1f" % (origin.publicID(), TGap))
            return False

    return True


def creationInfo(author, agencyID, time=None):
    if not time:
        now = seiscomp.core.Time.GMT()
        time = now
    ci = seiscomp.datamodel.CreationInfo()
    ci.setAuthor(author)
    ci.setAgencyID(agencyID)
    ci.setCreationTime(time)
    ci.setModificationTime(time)
    return ci



def configuredStreams(configModule, myName):

    # Determine which streams are configured for picking
    # according to "detecStream" and "detecLocid".
    #
    # Returns a list of (net, sta, detecLocid, detecStream[:2])
    # for all found items.
    items = []

    # loop over all configured stations
    for i in range(configModule.configStationCount()):
        # config for one station
        cfg = configModule.configStation(i)

        net, sta = cfg.networkCode(), cfg.stationCode()
        seiscomp.logging.debug("Config  %s  %s" % (net, sta))

        # client-specific setup for this station
        setup = seiscomp.datamodel.findSetup(cfg, myName, True)
        if not setup:
            seiscomp.logging.debug("no setup found")
            continue

        # break setup down do a set of parameters
        paramSet = setup.parameterSetID()
        seiscomp.logging.debug("paramSet "+paramSet)
        params = seiscomp.datamodel.ParameterSet.Find(paramSet)
        if not params:
            seiscomp.logging.debug("no params found")
            continue

        # search for "detecStream" and "detecLocid"
        detecStream, detecLocid = None, ""
        # We cannot look them up by name, therefore need
        # to check all available parameters.
        for k in range(params.parameterCount()):
            param = params.parameter(k)
            seiscomp.logging.debug("Config  %s  %s - %s %s"
                % (net, sta, param.name(), param.value()))
            if param.name() == "detecStream":
                detecStream = param.value()
            elif param.name() == "detecLocid":
                detecLocid = param.value()
        if not detecStream:
            # ignore stations without detecStream
            seiscomp.logging.debug("no detecStream found")
            continue

        # this may fail for future FDSN stream names
        if detecLocid == "":
            detecLocid = "--"
        item = (net, sta, detecLocid, detecStream[:2])
        seiscomp.logging.debug("Config  %s  %s %s" % (net, sta, str(item)))
        items.append(item)

    return items


def prepare(records):
    """
    Prepare waveforms for processing
    - sort records in time
    - remove duplicates
    """
    tp1 = tp2 = None
    filtered = list()
    # Assuming that all records are of the same stream, we compare start
    # and end times as a proxy for "identical record". The probability for
    # identical records is then high enough but we avoid headaches due to
    # different sequence numbers, which have indeed been observed in the
    # wild for otherwise identical records.
    for rec in sorted(records, key=lambda r: r.startTime()):
        if rec.startTime()==tp1 and rec.endTime()==tp2:
            continue
        filtered.append(rec)
        tp1 = rec.startTime()
        tp2 = rec.endTime()
    return filtered
