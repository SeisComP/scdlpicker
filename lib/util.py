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


import sys
import math
import yaml
import seiscomp.core
import seiscomp.datamodel
import seiscomp.logging
import seiscomp.io


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
    return n, s, l, c


def uncertainty(quantity):
    try:
        err = 0.5*(quantity.lowerUncertainty()+quantity.upperUncertainty())
    except ValueError:
        try:
            err = quantity.uncertainty()
        except ValueError:
            err = None
    return err


def authorOf(obj):
    try:
        return obj.creationInfo().author()
    except ValueError:
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
    if aziCount < 2:
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
        except ValueError:
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
    s = time.toString("%Y-%m-%dT%H:%M:%S.%f000000")
    s = s[:20+decimals].strip(".") + "Z"
    return s


def time2str(time, decimals=1):
    """
    Convert a seiscomp.core.Time to a string YYYY-MM-DD HH:MM:SS.s
    """
    s = time.toString("%Y-%m-%d %H:%M:%S.%f000000")
    s = s[:20+decimals]
    return s


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
        stat = obj.evaluationStatus()
        stat = seiscomp.datamodel.EEvaluationStatusNames.name(stat)
    except ValueError:
        stat = "NULL"

    try:
        mode = obj.evaluationMode()
        mode = seiscomp.datamodel.EEvaluationModeNames.name(mode)
    except ValueError:
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
    except ValueError:
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
    seiscomp.logging.debug(
        "dumping origin '%s' to XML file '%s'" % (
            origin.publicID(), xmlFileName))
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
    except ValueError:
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
            seiscomp.logging.debug(
                "Origin %s TGap=%.1f" % (origin.publicID(), TGap))
            return False

    return True


def clearAllArrivals(origin):
    while origin.arrivalCount():
        origin.removeArrival(0)


def clearAutomaticArrivals(origin):
    pos = 0
    while origin.arrivalCount() > pos:
        arr = origin.arrival(pos)
        pickID = arr.pickID()
        pick = seiscomp.datamodel.Pick.Find(pickID)
        if arr.weight() > 0.1 and pick is not None and manual(pick):
            # skip removal of arrival
            pos += 1
            continue
        origin.removeArrival(pos)

    # logging
    for arr in ArrivalIterator(origin):
        phase = arr.phase().code()
        pickID = arr.pickID()
        seiscomp.logging.debug("Keeping manual " + phase + " pick " + pickID)


def creationInfo(author, agencyID, creationTime=None):
    if not creationTime:
        now = seiscomp.core.Time.GMT()
        creationTime = now
    ci = seiscomp.datamodel.CreationInfo()
    ci.setAuthor(author)
    ci.setAgencyID(agencyID)
    ci.setCreationTime(creationTime)
    ci.setModificationTime(creationTime)
    return ci


def agencyID(origin):
    """
    Return the agency ID of the origin or None if unset.
    """
    try:
        return origin.creationInfo().agencyID()
    except ValueError:
        return


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
            seiscomp.logging.debug(
                "Config  %s  %s - %s %s" % (
                    net, sta, param.name(), param.value()))
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
        if rec.startTime() == tp1 and rec.endTime() == tp2:
            continue
        filtered.append(rec)
        tp1 = rec.startTime()
        tp2 = rec.endTime()
    return filtered


def pollRepickerResults(resultsDir):
    """
    Check if the repicker module has produced new results.

    If so, we read them and send them via the messaging.
    """

    yamlfilenames = list()

    for yamlfilename in resultsDir.glob("*.yaml"):
        yamlfilenames.append(yamlfilename)

    return yamlfilenames


def readRepickerResults(path):
    """
    Read repicking results from the specified YAML file.
    """

    picks = {}
    confs = {}
    comms = {}
    with open(path) as yamlfile:
        # Note that the repicker module may have produced more
        # than one repick per original pick. We pick the one
        # with the larger confidence value. Later on we may also
        # use the other picks e.g. as depth phases. Currently we
        # don't do that but it's a TODO item.

        for p in yaml.safe_load(yamlfile):
            pickID = p["publicID"]
            if seiscomp.datamodel.Pick.Find(pickID):
                # FIXME HACK FIXME
                seiscomp.logging.debug("FIXME: "+pickID)
            time = seiscomp.core.Time.FromString(p["time"], "%FT%T.%fZ")
            tq = seiscomp.datamodel.TimeQuantity()
            tq.setValue(time)
            net = p["networkCode"]
            sta = p["stationCode"]
            loc = p["locationCode"]
            cha = p["channelCode"]
            if len(cha) == 2:
                cha += "Z"
            wfid = seiscomp.datamodel.WaveformStreamID()
            wfid.setNetworkCode(net)
            wfid.setStationCode(sta)
            wfid.setLocationCode("" if loc == "--" else loc)
            wfid.setChannelCode(cha)

            model = p["model"].lower()
            if model in ["eqt", "eqtransformer"]:
                mth = "EQT"
            elif model in ["phn", "phasenet"]:
                mth = "PHN"
            else:
                mth = "XYZ"
            decimals = 2
            nslcstr = net + "." + sta + "." + loc + "." + cha[:2]
            timestr = time.toString("%Y%m%d.%H%M%S.%f000000")[:16+decimals]
            pickID = timestr + "-" + mth + "-" + nslcstr
            pick = seiscomp.datamodel.Pick(pickID)
            pick.setTime(tq)
            pick.setWaveformID(wfid)

            comments = []

            comment = seiscomp.datamodel.Comment()
            comment.setText(p["model"])
            comment.setId("dlmodel")
            comments.append(comment)

            conf = float(p["confidence"])

            comment = seiscomp.datamodel.Comment()
            comment.setText("%.3f" % p["confidence"])
            comment.setId("confidence")
            comments.append(comment)

            if pickID in picks:
                # only override existing pick with higher
                # confidence pick
                if conf <= confs[pickID]:
                    continue
            picks[pickID] = pick
            confs[pickID] = conf
            comms[pickID] = comments

        for pickID in picks:
            pick = picks[pickID]
            pick.setMethodID("DL")
            phase = seiscomp.datamodel.Phase()
            phase.setCode("P")
            pick.setPhaseHint(phase)
            pick.setEvaluationMode(seiscomp.datamodel.AUTOMATIC)

    return picks, comms


def gappy(waveforms, tolerance=0.5):
    """
    Check if there are any gaps in the waveforms. The waveforms
    argument is an ordered sequence of Record objects from the same
    stream, i.e. all with same NSLC.

    The tolerance is specified in multiples of the sampling interval.
    The default is half of a sampling interval.

    The records are assumed to be sorted in time and not multiplexed.
    """

    prev = None
    gapCount = 0
    for rec in waveforms:
        if prev:
            dt = float(rec.startTime() - prev.endTime())
            if abs(dt)*rec.samplingFrequency() > tolerance:
                gapCount += 1
        prev = rec
    return gapCount
