#!/usr/bin/env python3
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
import os
import yaml
import numpy
import seiscomp.core
import seiscomp.client
import seiscomp.datamodel
import seiscomp.logging
import seiscomp.math
import seiscomp.seismology
import scdlpicker.inventory
import scdlpicker.util
import scdlpicker.eventworkspace

from seiscomp import logging

from seiscomp.datamodel import \
    EventParameters, Event, Origin, Pick, \
    Notifier, \
    Magnitude, PublicObject, CreationInfo


#### Below is configuration that for the time being is hardcoded.

# That's me! This author ID will be written into all new picks.
author = "dlpicker"

# Agency ID to be written into all new picks.
agency = "GFZ"

# The acquisition will wait that long to finalize the acquisition
# of waveform time windows. The processing may be interrupted that
# long!
streamTimeout = 5

# This is the directory where all the event data are written to.
workingDir = "."

# This is the directory where all the event data are written to.
eventRootDir = "events"

# This is the directory in which we create symlinks pointing to
# data we need to work on.
spoolDir = "spool"

# This is the directory in which results are placed by the repicker
outgoingDir = "outgoing"

# ignore objects (picks, origins) from these authors
ignoredAuthors = [ "dl-reloc", author ]

# We may receive origins from other agencies, but don't want to
# process them. Add the agency ID's here. Any origin with agencyID
# in this list will be ignore.
ignoredAgencyIDs = []

# We normally never process empty origins. However, we may receive
# origins from other agencies that come without arrivals. We usually
# consider these trustworthy agencies and for such origins we do
# want to search for matching, previously missed picks.
emptyOriginAgencyIDs = ["EMSC", "BGR", "NEIC", "BMKG"]

# We can look for unpicked arrivals at additional stations.
tryUpickedStations = True

# We need a more convenient config for that:
netstaBlackList = [
# bad components
    ("WA", "ZON"),
]


ttt = seiscomp.seismology.TravelTimeTable()
ttt.setModel("iasp91")


def computeTravelTimes(delta, depth):
    arrivals = ttt.compute(0, 0, depth, 0, delta, 0, 0)
    return arrivals


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


def alreadyRepicked(pick):
    if pick.publicID().endswith("/repicked"):
        return True

    try:
        if pick.methodID() == "DL":
            return True
    except (AttributeError, ValueError):
        pass

    try:
        if pick.creationInfo().author() == author:
            return True
    except (AttributeError, ValueError):
        pass

    return False


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
            if abs(dt)/rec.samplingFrequency() > tolerance:
                gapCount += 1
    return gapCount


class OriginStreamApp(seiscomp.client.Application):

    def __init__(self, argc, argv):
        # adopt the defaults from the top of this script
        self.workingDir = None
        self.eventRootDir = None
        self.spoolDir = None
        self.outgoingDir = None
        self.sentDir = None

        self.ignoredAuthors = ignoredAuthors
        self.ignoredAgencyIDs = ignoredAgencyIDs
        self.emptyOriginAgencyIDs = emptyOriginAgencyIDs
        self.tryUpickedStations = tryUpickedStations

        super(OriginStreamApp, self).__init__(argc, argv)
        self.setDatabaseEnabled(True, True)
        self.setLoadInventoryEnabled(True)

        # we need the config to determine which streams are used for picking
        self.setLoadConfigModuleEnabled(True)

        # we want to stream waveform data and save the raw records
        self.setRecordStreamEnabled(True)
        # self.setRecordInputHint(seiscomp.core.Record.SAVE_RAW)

        self.workspaces = dict()
        self.acquisitionInProgress = False

        # Keep track of events that need to be processed. We process
        # one event at a time. In this dict we register the events
        # that require processing but we delay processing until
        # previous events are finished.
        self.pendingEvents = dict()



    def initConfiguration(self):
        # Called BEFORE validateParameters()

        seiscomp.logging.error("initConfigurarion(self)")
        if not super(OriginStreamApp, self).initConfiguration():
            return False

        try:
            self.workingDir = self.configGetString("mlpicker.workingDir")
        except RuntimeError:
            self.workingDir = None

        try:
            self.eventRootDir = self.configGetString("mlpicker.eventRootDir")
        except RuntimeError:
            self.eventRootDir = None

        try:
            self.spoolDir = self.configGetString("mlpicker.spoolDir")
        except RuntimeError:
            self.spoolDir = None

        try:
            self.outgoingDir = self.configGetString("mlpicker.outgoingDir")
        except RuntimeError:
            self.outgoingDir = None

        try:
            self.sentDir = self.configGetString("mlpicker.sentDir")
        except RuntimeError:
            self.sentDir = None

        try:
            self.ignoredAuthors = self.configGetStrings("mlpicker.ignoredAuthors")
        except RuntimeError:
            self.ignoredAuthors = ignoredAuthors

        try:
            self.ignoredAgencyIDs = self.configGetStrings("mlpicker.ignoredAgencyIDs")
        except RuntimeError:
            self.ignoredAgencyIDs = ignoredAgencyIDs

        try:
            self.emptyOriginAgencyIDs = self.configGetStrings("mlpicker.emptyOriginAgencyIDs")
        except RuntimeError:
            self.emptyOriginAgencyIDs = emptyOriginAgencyIDs

        try:
            self.tryUpickedStations =  self.configGetBool("mlpicker.tryUpickedStations")
        except RuntimeError:
            self.tryUpickedStations = tryUpickedStations
        return True


    def dumpConfiguration(self):
        info = seiscomp.logging.info
        info("workingDir = " + self.workingDir)
        info("eventRootDir = " + self.eventRootDir)
        info("spoolDir = " + self.spoolDir)
        info("outgoingDir = " + self.outgoingDir)
        info("sentDir = " + self.sentDir)

        info("ignoredAuthors = " + str(self.ignoredAuthors))
        info("ignoredAgencyIDs = " + str(self.ignoredAgencyIDs))
        info("emptyOriginAgencyIDs = " + str(self.emptyOriginAgencyIDs))
        info("tryUpickedStations = " + str(self.tryUpickedStations))


    def createCommandLineDescription(self):
        self.commandline().addGroup("Config")
        self.commandline().addStringOption(
            "Config", "working-dir,d", "path of the working directory")

        self.commandline().addGroup("Test")
        self.commandline().addStringOption(
            "Test", "event,E", "ID of event to test")

        return True


    def validateParameters(self):
        """
        Command-line parameters
        """
        if not super(OriginStreamApp, self).validateParameters():
            return False

        try:
            print("TRYING optionString('working-dir')")
            self.workingDir = self.commandline().optionString("working-dir")
            print("FOUND  optionString('working-dir')")
        except RuntimeError as e:
            pass

        self.setMessagingEnabled(True)
        if not self.commandline().hasOption("event"):
            # not in event mode -> configure the messaging
            self.setMessagingEnabled(True)
            self.setPrimaryMessagingGroup("MLTEST")
            self.addMessagingSubscription("PICK")
            self.addMessagingSubscription("LOCATION")
            self.addMessagingSubscription("EVENT")

        try:
            self.eventRootDir = self.commandline().optionString("event-dir")
        except RuntimeError as e:
            pass

        try:
            self.spoolDir = self.commandline().optionString("spool-dir")
        except RuntimeError as e:
            pass

        try:
            self.outgoingDir = self.commandline().optionString("outgoing-dir")
        except RuntimeError as e:
            pass

        try:
            self.sentDir = self.commandline().optionString("sent-dir")
        except RuntimeError as e:
            pass

        if not self.eventRootDir:
            self.eventRootDir = os.path.join(self.workingDir, "events")

        if not self.spoolDir:
            self.spoolDir = os.path.join(self.workingDir, "spool")

        if not self.outgoingDir:
            self.outgoingDir = os.path.join(self.workingDir, "outgoing")

        if not self.sentDir:
            self.sentDir = os.path.join(self.workingDir, "sent")

        return True


    def init(self):
        if not super(OriginStreamApp, self).init():
            return False

        self.setupFolders()

        self.setupComponents()
        self.configuredStreams = self._getConfiguredStreams()

        return True


    def handleTimeout(self):

        self.pollResults()

        for eventID in sorted(self.pendingEvents.keys()):
            event = self.pendingEvents.pop(eventID)
            self.processEvent(event)


    def _getConfiguredStreams(self):
        # determine which streams are configured for picking
        # according to "detecStream" and "detecLocid".
        items = []

        mod = self.configModule()
        me = self.name()

        # iterate over all configured stations
        for i in range(mod.configStationCount()):
            # config for one station
            cfg = mod.configStation(i)

            net, sta = cfg.networkCode(), cfg.stationCode()
            logging.debug("Config  %s  %s" % (net, sta))

            # client-specific setup for this station
            setup = seiscomp.datamodel.findSetup(cfg, me, True)
            if not setup:
                logging.debug("no setup found")
                continue

            # break setup down do a set of parameters
            paramSet = setup.parameterSetID()
            logging.debug("paramSet "+paramSet)
            params = seiscomp.datamodel.ParameterSet.Find(paramSet)
            if not params:
                logging.debug("no params found")
                continue

            # search for "detecStream" and "detecLocid"
            detecStream, detecLocid = None, ""
            # We cannot look them up by name, therefore need
            # to check all available parameters.
            for k in range(params.parameterCount()):
                param = params.parameter(k)
                logging.debug("Config  %s  %s - %s %s"
                    % (net, sta, param.name(), param.value()))
                if param.name() == "detecStream":
                    detecStream = param.value()
                elif param.name() == "detecLocid":
                    detecLocid = param.value()
            if not detecStream:
                # ignore stations without detecStream
                logging.debug("no detecStream found")
                continue

            # this may fail for future FDSN stream names
            if detecLocid == "":
                detecLocid = "--"
            item = (net, sta, detecLocid, detecStream[:2])
            logging.debug("Config  %s  %s %s" % (net, sta, str(item)))
            items.append(item)

        return items


    def findUnpickedStations(self, origin, maxDelta, picks):
        """
        Find stations within maxDelta from origin which are not
        represented by any of the specified picks.
        """
        logging.debug("findUnpickedStations for maxDelta=%g" % maxDelta)
        elat = origin.latitude().value()
        elon = origin.longitude().value()

        net_sta_blacklist = []
        for pickID in picks:
            pick = picks[pickID]
            n, s, l, c = scdlpicker.util.nslc(pick)
            net_sta_blacklist.append((n, s))

        predictedPicks = {}

        now = seiscomp.core.Time.GMT()
        inv = seiscomp.client.Inventory.Instance().inventory()
        inv = scdlpicker.inventory.InventoryIterator(inv, now)
        for network, station, location, stream in inv:
            n =  network.code()
            s =  station.code()
            l = location.code()
            c =   stream.code()
            c = c[:2]
            # nslc = (n, s, l, c)
            if (n, s) in net_sta_blacklist:
                continue
            if (n, s, "--" if l=="" else l, c) not in self.configuredStreams:
                continue
            slat = station.latitude()
            slon = station.longitude()
            delta, a, b = seiscomp.math.delazi(elat, elon, slat, slon)
            if delta > maxDelta:
                continue

            arrivals = computeTravelTimes(delta, origin.depth().value())
            firstArrival = arrivals[0]
            time = origin.time().value() + \
                seiscomp.core.TimeSpan(firstArrival.time)
            timestamp = time.toString("%Y%m%d.%H%M%S.%f000000")[:18]
            pickID = timestamp + "-PRE-%s.%s.%s.%s" % (n, s, l, c)
            predictedPick = seiscomp.datamodel.Pick(pickID)
            phase = seiscomp.datamodel.Phase()
            phase.setCode("P")
            predictedPick.setPhaseHint(phase)
            q = seiscomp.datamodel.TimeQuantity(time)
            predictedPick.setTime(q)
            wfid = seiscomp.datamodel.WaveformStreamID()
            wfid.setNetworkCode(n)
            wfid.setStationCode(s)
            wfid.setLocationCode(l)
            wfid.setChannelCode(c+"Z")
            predictedPick.setWaveformID(wfid)
            # We do not set the creation info here.
            if pickID not in predictedPicks:
                logging.debug("predicted pick %s" % pickID)
                predictedPicks[pickID] = predictedPick
        return predictedPicks


    def setupFolders(self):
        # if necessary, create some needed folders at startup
        for d in [self.eventRootDir, self.spoolDir, self.outgoingDir, self.sentDir]:
            os.makedirs(d, exist_ok=True)


    def setupComponents(self):
        # dict with n,s,l,c[:2] as key, and list of c[2]'s as values
        self.components = dict()

        now = seiscomp.core.Time.GMT()
        inv = seiscomp.client.Inventory.Instance().inventory()
        inv = scdlpicker.inventory.InventoryIterator(inv, now)
        for network, station, location, stream in inv:
            n =  network.code()
            s =  station.code()
            if (n,s) in netstaBlackList:
                continue
            l = location.code()
            c =   stream.code()
            if l == "":
                l = "--"
            comp = c[2]
            c = c[:2]
            nslc = (n, s, l, c)
            if nslc not in self.components:
                self.components[nslc] = []
            self.components[nslc].append(comp)


    def _loadEvent(self, publicID):
        # load a bare Event object from database
        tp = Event
        obj = self.query().loadObject(tp.TypeInfo(), publicID)
        obj = tp.Cast(obj)
        if obj is None:
            logging.error("unknown Event '%s'" % publicID)
        return obj


    def _loadOrigin(self, publicID):
        # load an Origin object from database
        tp = Origin
        obj = self.query().loadObject(tp.TypeInfo(), publicID)
        obj = tp.Cast(obj)
        if obj is None:
            logging.error("unknown Origin '%s'" % publicID)
        return obj


    def _loadWaveformsForPicks(self, picks, event):

        self.acquisitionInProgress = True

        datarequest = list()
        for pickID in picks:
            pick = picks[pickID]
            wfid = pick.waveformID()
            net = wfid.networkCode()
            sta = wfid.stationCode()
            loc = wfid.locationCode()
            if loc == "":
                loc = "--"
            cha = wfid.channelCode()

            # Sometimes manual picks produced by scolv have only
            # "BH" etc. as channel code. IIRC this is to accomodate
            # 3-component picks where "BHZ" etc. would be misleading.
            # But here it doesn't matter for the data request.
            if len(cha) == 2:
                cha = cha+"Z"

            # avoid requesting data that we have saved already
            eventID = event.publicID()
            key = "%s.%s.%s.%s" % (net, sta, loc, cha)
            mseedFileName = os.path.join(self.eventRootDir, eventID, key+".mseed")
            if os.path.exists(mseedFileName):
                # TODO: Check data completeness, otherwise do request
                continue

            t0 = pick.time().value()
            beforeP = afterP = 60.  # temporarily
            t1 = t0 + seiscomp.core.TimeSpan(-beforeP)
            t2 = t0 + seiscomp.core.TimeSpan(afterP)
            datarequest.append((t1, t2, net, sta, loc, cha))
            t1 = scdlpicker.util.isotimestamp(t1)
            t2 = scdlpicker.util.isotimestamp(t2)
            logging.debug("REQUEST %-2s %-5s %-2s %-2s %s %s"
                % (net, sta, loc, cha, t1, t2))

        waveforms = dict()

        if not datarequest:
            self.acquisitionInProgress = False
            return waveforms  # empty

        # request waveforms and dump them to one file per stream
        logging.info("RecordStream start")
        stream = seiscomp.io.RecordStream.Open(self.recordStreamURL())
        stream.setTimeout(streamTimeout)
        streamCount = 0
        for t1, t2, net, sta, loc, cha in datarequest:
            try:
                components = self.components[(net, sta, loc, cha[:2])]
            except KeyError as e:
                # This may occur if a station was (1) blacklisted or
                # (2) added to the processing later on.
                # Either way we skip this pick.
                continue
            for c in components:
                _loc = "" if loc == "--" else loc
                stream.addStream(net, sta, _loc, cha[:2] + c, t1, t2)
                streamCount += 1
        logging.info("RecordStream: requested %d streams" % streamCount)
        for rec in scdlpicker.util.RecordIterator(stream):
            if not rec.streamID() in waveforms:
                waveforms[rec.streamID()] = []

            # append binary (raw) MiniSEED record to data string
            waveforms[rec.streamID()].append(rec)
        count = 0
        for key in waveforms:
            count += len(waveforms[key])
        logging.debug("RecordStream: received  %d records for %d streams"
            % (count, len(waveforms.keys())))

        # remove gappy streams
        gappyStreams = []
        for streamID in waveforms:
            if gappy(waveforms[streamID], tolerance=1.):
                gappyStreams.append(streamID)
        for streamID in gappyStreams:
            logging.warning("Gappy stream "+streamID+" ignored")
            del waveforms[streamID]

        # determine streams for which we don't have 3 components
        streamIDs = dict()
        for streamID in waveforms:
            firstRecord = waveforms[streamID][0]
            n,s,l,c = scdlpicker.util.nslc(firstRecord)
            if (n,s,l) not in streamIDs:
                streamIDs[(n,s,l)] = []
            streamIDs[(n,s,l)].append(streamID)

        # for each complete NSL stream there should be 3 streamID's
        for nsl in streamIDs:
            if len(streamIDs[nsl]) < 3:
                for streamID in streamIDs[nsl]:
                    del waveforms[streamID]
                    logging.warning("Incomplete stream "+streamID+" ignored")

        self.acquisitionInProgress = False
        return waveforms



    def testEvent(self, eventID,
            skipManualOrigins=True,
            preferredOriginOnly=False):
        """
        Test the module for the event with the specified ID.

        Real-time is simulated by loading all origins and then
        iterate over these origins in the order of their creation.
        Then for each origin we load the data from the waveform
        server but only for the picks not yet loaded.
        """
        # load event and preferred origin
        event = self._loadEvent(eventID)
        if not event:
            logging.error("Failed to load event "+eventID)
            return False

        logging.debug("Loaded event "+eventID)

        workspace = self.workspaces[eventID] = scdlpicker.eventworkspace.EventWorkspace()
        workspace.event = event
        workspace.origin = None
        workspace.all_picks = dict()

        origins = list()

        if preferredOriginOnly is True:
            origin = self.query().loadObject(
                Origin.TypeInfo(), event.preferredOriginID())
            origin = Origin.Cast(origin)
            origins.append(origin)
        else:
            tmp_origins = list()
            for origin in self.query().getOrigins(eventID):
                try:
                    # hack to acquire ownership
                    origin = Origin.Cast(origin)
                    assert origin is not None
                    origin.creationInfo().creationTime()
                except ValueError:
                    continue
                except AssertionError:
                    continue

                if manual(origin) and skipManualOrigins is True:
                    logging.debug("Skipping manual origin " + origin.publicID())
                    continue

                tmp_origins.append(origin)

            # two origin loops to prevent nested DB calls
            for origin in tmp_origins:

                # This loads everything including the arrivals
                # but is very slow
                # origin = self.query().loadObject(
                #     Origin.TypeInfo(), origin.publicID())
                # origin = Origin.Cast(origin)
                # if not origin: continue

                # See if the origin has arrivals. Of not, try to load
                # arrivals from database. If still no arrivals, give up.
                # loadArrivals() is much faster than the more
                # comprehensive loadObject()
                if origin.arrivalCount() == 0:
                    self.query().loadArrivals(origin)
                if origin.arrivalCount() == 0:
                    continue

                origins.append(origin)

        logging.debug("Loaded %d origin(s)" % len(origins))

        sorted_origins = sorted(
            origins, key=lambda origin: origin.creationInfo().creationTime())

        for origin in sorted_origins:
            if self.isExitRequested():
                # e.g. Ctrl-C
                return True

            workspace.origin = origin
            self.processOrigin(origin, event)
            workspace.dump()

        return True


    def addObject(self, parentID, obj):
        # called by the Application class if a new object is received
        event = Event.Cast(obj)
        if scdlpicker.util.valid(event):
            self.pendingEvents[event.publicID()] = event


    def updateObject(self, parentID, obj):
        # called by the Application class if an updated object is received
        event = Event.Cast(obj)
        if scdlpicker.util.valid(event):
            self.pendingEvents[event.publicID()] = event


    def cleanup(self, timeout=30*3600):
        # timeout = 86400 # one day
        now = seiscomp.core.Time.GMT()
        tmin = now-seiscomp.core.TimeSpan(timeout)
        blacklist = []
        for eventID in self.workspaces:
            workspace = self.workspaces[eventID]
            if workspace.origin.time().value() < tmin:
                blacklist.append(eventID)
        for eventID in blacklist:
            del self.workspaces[eventID]


    def processOrigin(self, origin, event):
        """
        Process the given origin in the context of the given event.
        Used in both real-time and test mode.
        """
        if origin.creationInfo().agencyID() in ignoredAgencyIDs:
            return True

        originID = origin.publicID()

        # Ignore origins without any arrivals except if this origin
        # is explicitly white listed
        if origin.arrivalCount() == 0:
            if origin.creationInfo().agencyID() not in emptyOriginAgencyIDs:
                logging.debug("No arrivals in origin "+originID +" -> skipped")
                return

        logging.debug("processing origin "+originID)

        now = seiscomp.core.Time.GMT()

        workspace = self.workspaces[event.publicID()]

        # Find out which picks are new picks.
        #
        # Now all picks for which we don't have a DL pick are
        # considered new. Therefore also picks that were
        # received previously but failed to process due to
        # missing data are now re-processed.
        workspace.new_picks.clear()

        associated_picks = []
        for obj in self.query().getPicks(originID):
            pick = Pick.Cast(obj)

            # prevent a pick from myself from being repicked
            try:
                if pick.creationInfo().author() in ignoredAuthors:
                    continue
            except:
                continue

            associated_picks.append(pick)

        for pick in associated_picks:

            pickID = pick.publicID()
            # if pickID in workspace.mlpicks:
            #     logging.debug("I already have a DL pick for "+pickID)
            if pickID not in workspace.all_picks:
                workspace.all_picks[pickID] = pick

            # We usually don't re-pick manual picks.
            # Perhaps add option to allow that.
            if manual(pick):
                continue
            
            if alreadyRepicked(pick): 
                continue

            logging.debug("adding new pick "+pickID)
            workspace.new_picks[pickID] = pick

        try:
            magnitudeID = event.preferredMagnitudeID()
        except ValueError:
            logging.warning("Event.preferredMagnitudeID not set")
            return

        tmp = "%d" % len(workspace.new_picks) if workspace.new_picks else "no"
        logging.debug(tmp+" new picks")

        waveforms = self._loadWaveformsForPicks(workspace.new_picks, event)
        for streamID in waveforms:
            workspace.waveforms[streamID] = waveforms[streamID]

        # #########################################################
        # Create predictions from theoretical arrivals for stations
        # where we don't (yet) have measured picks.
        # #########################################################

        if origin.creationInfo().agencyID() in emptyOriginAgencyIDs:
            # TODO: Make this distance magnitude dependent
            maxDelta = 100.
        else:
            # The goal here is to limit the maximum distance to a
            # reasonable value reflecting the pick distribution
            # w.r.t. distance. E.g. if we have only regional picks
            # up to 8 degrees distance then we don't look at
            # teleseismic phases. Whereas if we have "a few"
            # teleseismic phases, there is a good chance to obtain
            # additional picks from all over the teleseismic
            # distance range (hard limit here: 100 degrees).
            maxDelta = 0.
            origin = workspace.origin
            # create a sorted list of distances of all *used* arrivals
            delta = []
            for i in range(origin.arrivalCount()):
                arr = origin.arrival(i)
                if arr.weight() < 0.5:
                    continue
                delta.append(arr.distance())
            delta.sort()

            # As maximum distance use the average distance
            # of the five farthest used arrivals.
            maxDelta = numpy.average(delta[-3:])

            # If the distance exceeds 50 degrees, it is promising
            # to look at the entire P distance range.
            if maxDelta > 40:
                maxDelta = 100

        if tryUpickedStations:
            # TODO: delay
            seiscomp.logging.debug("tryUpickedStations A XXX")
            predictedPicks = self.findUnpickedStations(
                workspace.origin, maxDelta, workspace.all_picks)
            seiscomp.logging.debug("%d predicted picks" % len(predictedPicks))
            if len(predictedPicks) > 0:
                for pickID in predictedPicks:
                    pick = predictedPicks[pickID]
                    if pickID not in workspace.all_picks:
                        workspace.all_picks[pickID] = pick
                        workspace.new_picks[pickID] = pick
                seiscomp.logging.debug("tryUpickedStations B XXX")

                seiscomp.logging.debug("tryUpickedStations B XXX2")
                waveforms = self._loadWaveformsForPicks(workspace.new_picks, event)
                seiscomp.logging.debug("tryUpickedStations B XXX3")
                for streamID in waveforms:
                    workspace.waveforms[streamID] = waveforms[streamID]
                seiscomp.logging.debug("tryUpickedStations B XXX4")
                seiscomp.logging.debug("tryUpickedStations C XXX")

        # We dump the waveforms to files in order to
        # read them as miniSEED files into ObsPy. This
        # is the SeisComP-to-ObsPy iterface so to say.
        workspace.dump(eventRootDir=self.eventRootDir, spoolDir=self.spoolDir)


    def processEvent(self, event):
        """
        Processes the event and the current preferred origin.
        This is called only in real-time usage.
        """
        eventID = event.publicID()

        # Register an EventWorkspace instance if needed
        if eventID not in self.workspaces:
            self.workspaces[eventID] = scdlpicker.eventworkspace.EventWorkspace()
        workspace = self.workspaces[eventID]

        # Load a more complete version of the event
        event = self._loadEvent(eventID)
        workspace.event = event
        originID = event.preferredOriginID()
        if workspace.origin:
            if originID == workspace.origin.publicID():
                logging.debug(
                    "Event "+eventID+": no change of preferred origin")
                return
        origin = self._loadOrigin(originID)
        workspace.origin = origin
        self.processOrigin(origin, event)
        self.cleanup()


    def _creationInfo(self):
        ci = CreationInfo()
        ci.setAuthor(author)
        ci.setAgencyID(agency)
        ci.setCreationTime(seiscomp.core.Time.GMT())
        return ci


    def readResults(self, path):
        """
        Read repicking results from the specified YAML file.
        """

        picks = {}
        confs = {}
        with open(path) as yf:
            ci = self._creationInfo()

            # Note that the repicker module may have produced more
            # than one repick per original pick. We pick the one
            # with the larger confidence value. Later on we may also
            # use the other picks e.g. as depth phases. Currently we
            # don't do that but it's a TODO item.

            for p in yaml.safe_load(yf):
                pickID = p["publicID"]
                pick = Pick(pickID)
                time = seiscomp.core.Time.FromString(p["time"], "%FT%T.%fZ")
                tq = seiscomp.datamodel.TimeQuantity()
                tq.setValue(time)
                pick.setTime(tq)
                n = p["networkCode"]
                s = p["stationCode"]
                l = p["locationCode"]
                c = p["channelCode"]
                if len(c) == 2:
                    c += "Z"
                wfid = seiscomp.datamodel.WaveformStreamID()
                wfid.setNetworkCode(n)
                wfid.setStationCode(s)
                wfid.setLocationCode("" if l == "--" else l)
                wfid.setChannelCode(c)
                pick.setWaveformID(wfid)

                conf = float(p["confidence"])
                
                if pickID in picks:
                    # only override existing pick with higher
                    # confidence pick
                    if conf <= confs[pickID]:
                        continue
                picks[pickID] = pick
                confs[pickID] = conf
                

            for pickID in picks:
                pick = picks[pickID]
                pick.setCreationInfo(ci)
                pick.setMethodID("DL")
                phase = seiscomp.datamodel.Phase()
                phase.setCode("P")
                pick.setPhaseHint(phase)
                pick.setEvaluationMode(seiscomp.datamodel.AUTOMATIC)

        return picks


    def pollResults(self):
        """
        Check if the repicker module has produced new results.

        If so, we read them and send them via the messaging.
        """

        todolist = list()

        for item in os.listdir(self.outgoingDir):
            if not item.endswith(".yaml"):
                continue
            path = os.path.join(self.outgoingDir, item)
            todolist.append(path)

        for path in sorted(todolist):
            logging.info("pollResults: working on "+path)
            picks = self.readResults(path)

            ep = EventParameters()
            Notifier.Enable()
            for pickID in picks:
                pick = picks[pickID]
                ep.add(pick)
            msg = Notifier.GetMessage()
            Notifier.Disable()
            if self.connection().send(msg):
                for pickID in picks:
                    logging.info("sent "+pickID)
            else:
                for pickID in picks:
                    logging.info("failed to send "+pickID)
            d, f = os.path.split(path)
            sent = os.path.join(self.sentDir, f)
            os.rename(path, sent)


    def run(self):
        self.dumpConfiguration()

        try:
            eventID = self.commandline().optionString("event")
        except RuntimeError as e:
            # A bit strange exception, but we can't change it.
            assert str(e) == "Invalid type for cast"
            eventID = None

        if eventID:
            logging.info("In single-event mode. Event is "+eventID)
            return self.testEvent(eventID)

        # enter real-time mode
        self.enableTimer(1)
        PublicObject.SetRegistrationEnabled(False)

        return super(OriginStreamApp, self).run()


def main():
    app = OriginStreamApp(len(sys.argv), sys.argv)
    app()


if __name__ == "__main__":
    main()
