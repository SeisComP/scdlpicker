#!/usr/bin/env seiscomp-python
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

"""
This is a very simple relocator that

- reads the preferred origin of an event from the database
- tries to find all matching DL picks based on predicted
  travel times
- relocates based on *only* the DL-picks
- sends the results

Missing

- amplitudes
- and therefore also magnitudes
- and therefore origin will not become preferred
"""



import sys, time
import seiscomp.core
import seiscomp.client
import seiscomp.datamodel
import seiscomp.io
import seiscomp.logging
import seiscomp.math
import seiscomp.seismology
import scdlpicker.dbutil
import scdlpicker.util
import scdlpicker.inventory


maxResidual = 2.5
maxRMS = 1.7


def findStation(inventory, nslc, time):
    net, sta, loc, cha = nslc

    for item in scdlpicker.inventory.InventoryIterator(inventory, time=time):
        network, station, location, stream = item

        # return first-matching station object
        if network.code() == net and station.code() == sta:
            return station


def uncertainty(quantity):
    try:
        err = 0.5*(quantity.lowerUncertainty()+quantity.upperUncertainty())
    except:
        try:
            err = quantity.uncertainty()
        except:
            err = None
    return err


def time2str(time, decimals=1):
    """
    Convert a seiscomp.core.Time to a string
    """
    return time.toString("%Y-%m-%d %H:%M:%S.%f000000")[:20+decimals]


def author(obj):
    return obj.creationInfo().author()


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


def ArrivalIterator(origin):
    for i in range(origin.arrivalCount()):
        yield origin.arrival(i)


def summary(obj, withPicks=False):
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


def trimLargestResidual(origin, maxResidual):
    """
    Identify the arrival with the largest residual and disable
    it in case the residual exceeds maxResidual.

    Returns True if such an arrival was found and disables,
    otherwise False.
    """

    largest = None
    for arr in ArrivalIterator(origin):

        if not arr.timeUsed():
            continue

        try:
            if arr.distance() > 105:
                arr.setTimeUsed(False)
                arr.setWeight(0.)
                continue
        except ValueError as e:
            seiscomp.logging.error(arr.pickID())
            raise

        if largest is None:
            largest = arr
            continue

        if abs(arr.timeResidual()) > abs(largest.timeResidual()):
            largest = arr

    largestResidual = abs(largest.timeResidual())
    seiscomp.logging.debug("largest residual %5.2f s" % largestResidual)
    if largestResidual > maxResidual:
        largest.setTimeUsed(False)
        largest.setWeight(0.)
        return True
    else:
        False



class RelocatorApp(seiscomp.client.Application):

    def __init__(self, argc, argv):
        seiscomp.client.Application.__init__(self, argc, argv)
        self.setMessagingEnabled(True)
        self.setDatabaseEnabled(True, True)
        self.setLoadInventoryEnabled(True)
        self.setPrimaryMessagingGroup("LOCATION")

        self.minimumDepth = 10.
        self.maxResidual = 2.5


    def createCommandLineDescription(self):
        seiscomp.client.Application.createCommandLineDescription(self)
        self.commandline().addGroup("Target");
        self.commandline().addStringOption("Target", "event,E",  "load the specified event");
        self.commandline().addStringOption("Target", "pick-authors", "space-separated whitelist of pick authors");
        self.commandline().addDoubleOption("Target", "fixed-depth",
"fix the depth at the specified value (in kilometers)");
        self.commandline().addDoubleOption("Target", "max-residual", "limit the individual pick residual to the specified value (in seconds)");
        self.commandline().addDoubleOption("Target", "max-rms", "limit the pick residual RMS to the specified value (in seconds)");



    def _load(self, oid, tp):
        assert oid is not None
        obj = self.query().loadObject(tp.TypeInfo(), oid)
        tmp = tp.Cast(obj)
        return tmp

    def _load_event(self, eventID, originIDs=[]):
        seiscomp.logging.info("Loading event %s from database" % eventID)
        event = self._load(eventID, seiscomp.datamodel.Event)

        if not originIDs:
            originIDs = [ event.preferredOriginID() ]

        origins = []
        picks = {}
        for originID in originIDs:

            origin = self._load(originID, seiscomp.datamodel.Origin)
            origins.append(origin)

            #### Not needed:
            # self.query().loadArrivals(origin)

            seiscomp.logging.info("Loading corresponding picks")
            for arr in ArrivalIterator(origin):
                pid = arr.pickID()
                if pid in picks:
                    continue
                obj = self._load(pid, seiscomp.datamodel.Pick)
                picks[pid] = obj
            seiscomp.logging.info("Loaded %d picks" % len(picks))

            seiscomp.logging.info("Loading completed")
        return event, origins, picks


    def loadPicks(self, origin, authorIDs):
        etime = origin.time().value()
        elat = origin.latitude().value()
        elon = origin.longitude().value()

        # clear all arrivals
        while origin.arrivalCount():
            origin.removeArrival(0)

        # uses the iasp91 tables by default
        ttt = seiscomp.seismology.TravelTimeTable()

        station = dict()
        inventory = seiscomp.client.Inventory.Instance().inventory()
        for item in scdlpicker.inventory.InventoryIterator(inventory, time=etime):
            net, sta, loc, stream = item
            n = net.code()
            s = sta.code()
            if (n,s) in station:
                continue
            station[n,s] = sta

        startTime = origin.time().value()
        endTime = startTime + seiscomp.core.TimeSpan(1200.)
        picks = scdlpicker.dbutil.loadPicksForTimespan(self.query(), startTime, endTime)	

        result = []
        for pickID in picks:
            pick = picks[pickID]
            if pick.creationInfo().author() not in authorIDs:
                continue
            n,s,l,c = scdlpicker.util.nslc(pick)
            try:
                sta = station[n,s]
            except KeyError as e:
                seiscomp.logging.error(str(e))
                continue
            slat = sta.latitude()
            slon = sta.longitude()

            delta, az, baz = seiscomp.math.delazi_wgs84(elat, elon, slat, slon)

            arrivals = ttt.compute(0, 0, origin.depth().value(), 0, delta, 0, 0)
            arr = arrivals[0]

            theo = etime + seiscomp.core.TimeSpan(arr.time)
            dt = float(pick.time().value() - theo)

            # initially we grab more picks than within the final
            # residual range and trim the residuals later.
            if -2*maxResidual < dt < 2*maxResidual:
                result.append(pick)

                phase = seiscomp.datamodel.Phase()
                phase.setCode("P")
                arr = seiscomp.datamodel.Arrival()
                arr.setPhase(phase)
                arr.setPickID(pickID)
                arr.setTimeUsed(True)
                arr.setWeight(1.)
                origin.add(arr)

        return origin, result


    def run(self):

        # seiscomp.seismology.LocatorInterface
        # seiscomp.seismology.LocSAT

        seiscomp.datamodel.PublicObject.SetRegistrationEnabled(True)

        pickAuthors = ["dlpicker"]
#       pickAuthors = self.commandline().optionString("pick-authors")
#       pickAuthors = pickAuthors.split()

        try:
            self.maxResidual = self.commandline().optionDouble("max-residual")
        except RuntimeError:
            self.maxResidual = maxResidual

        try:
            self.maxRMS = self.commandline().optionDouble("max-rms")
        except RuntimeError:
            self.maxRMS = maxRMS

        eventID = self.commandline().optionString("event")
        event  = scdlpicker.dbutil.loadEvent(self.query(), eventID)
        origin = scdlpicker.dbutil.loadOrigin(self.query(), event.preferredOriginID())

        # Load all picks for a matching time span, independent of
        # their association. 
        origin, picks = self.loadPicks(origin, authorIDs=["dlpicker"])

        for arr in ArrivalIterator(origin):
            pickID = arr.pickID()
            if not seiscomp.datamodel.Pick.Find(pickID):
                seiscomp.logging.warning("Pick '"+pickID+"' NOT FOUND")


        summary(origin)

        loc = seiscomp.seismology.LocatorInterface.Create("LOCSAT")
        fixed = not uncertainty(origin.depth())
        fixed = self.commandline().hasOption("fixed-depth")
        if fixed:
            dep = self.commandline().optionDouble("fixed-depth")
            loc.useFixedDepth(True)
            loc.setFixedDepth(dep)
            seiscomp.logging.info("Using fixed depth of %g km" % dep)
        else:
            loc.useFixedDepth(False)

        now = seiscomp.core.Time.GMT();

        while True:
            try:
                relocated = loc.relocate(origin)
            except RuntimeError:
                seiscomp.logging.error("Failed to relocate origin")
                # We sometimes observe that a locator fails to
                # relocate an origin. At the moment we cannot
                # repair that and therefore have to give up. But
                # before that we dump the origin and picks to XML.
                summary(origin, withPicks=True)
                timestamp = scdlpicker.util.isotimestamp(now)
                dumpOriginXML(origin, "%s-%s-failed-relocation.xml"
                    % (eventID, timestamp))
                raise

            if not fixed and \
               relocated.depth().value() < self.minimumDepth:
                loc.useFixedDepth(True)
                loc.setFixedDepth(self.minimumDepth)
                relocated = loc.relocate(origin)
                loc.useFixedDepth(False)

            if not trimLargestResidual(relocated, maxResidual):
                break

            origin = relocated

        crea = seiscomp.datamodel.CreationInfo()
        crea.setAuthor("dl-reloc")
        crea.setAgencyID("GFZ")
        crea.setCreationTime(now)
        crea.setModificationTime(now)
        relocated.setCreationInfo(crea)
        relocated.setEvaluationMode(seiscomp.datamodel.AUTOMATIC)

        ep = seiscomp.datamodel.EventParameters()
        seiscomp.datamodel.Notifier.Enable()
        ep.add(relocated)
        event.add(seiscomp.datamodel.OriginReference(relocated.publicID()))
        msg = seiscomp.datamodel.Notifier.GetMessage()
        seiscomp.datamodel.Notifier.Disable()

        if self.connection().send(msg):
            seiscomp.logging.info("sent "+relocated.publicID())

        summary(relocated)

#       ar = seiscomp.io.XMLArchive()
#       ar.setFormattedOutput(True)
#       ar.create("out.xml")
#       ar.writeObject(ep)
#       ar.close()
        return True


if __name__ == "__main__":
    app = RelocatorApp(len(sys.argv), sys.argv)
    status = app()
    sys.exit(status)
