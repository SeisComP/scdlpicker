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
This is a very simple online relocator that

- listens to the messaging for origins
- reads the preferred origin of an event from the database
- tries to find all matching DL picks based on predicted
  travel times
- relocates based on *only* the DL-picks
- sends the results
"""


import sys
import time
import seiscomp.core
import seiscomp.client
import seiscomp.datamodel
import seiscomp.logging
import seiscomp.math
import seiscomp.seismology
import scdlpicker.dbutil
import scdlpicker.util
import scdlpicker.inventory
import scdlpicker.relocation


maxResidual = 2.5
maxRMS = 1.7


def authorOf(obj):
    try:
        return obj.creationInfo().author()
    except:
        return None


def arrivalCount(org, minArrivalWeight=0.5):
    count = 0
    for i in range(org.arrivalCount()):
        arr = org.arrival(i)
        if arr.weight() >= minArrivalWeight:
            count += 1
    return count


def quality(origin):
    # similar role of origin score in scautoloc

    return arrivalCount(origin) # to be improved 


def qualified(origin):
    # Check whether an origin meets certain criteria.
    #
    # This is work in progress and currently very specific to
    # the global monitoring at GFZ. In other contexts this test
    # may have to be adapted or skipped.

    if origin.arrivalCount() > 0:
        # Ensure sufficient azimuthal coverage. 
        TGap = scdlpicker.util.computeTGap(origin, maxDelta=90)
        if TGap > 270:
            seiscomp.logging.debug("Origin %s TGap=%.1f" % (origin.publicID(), TGap))
            return False

    return True


class RelocatorApp(seiscomp.client.Application):

    def __init__(self, argc, argv):
        seiscomp.client.Application.__init__(self, argc, argv)
        self.setMessagingEnabled(True)
        self.setDatabaseEnabled(True, True)
        self.setLoadInventoryEnabled(True)
        self.setPrimaryMessagingGroup("LOCATION")
        self.addMessagingSubscription("LOCATION")
        self.addMessagingSubscription("EVENT")

        self.minimumDepth = 10.
        self.maxResidual = 2.5

        # Keep track of changes of the preferred origin of each event
        self.preferredOrigins = dict()

        # Keep track of events that need to be processed. We process
        # one event at a time. In this dict we register the events
        # that require processing but we delay processing until
        # previous events are finished.
        self.pendingEvents = dict()

        self.origins = dict()

        # latest relocated origin per event
        self.relocated = dict()


    def createCommandLineDescription(self):
        seiscomp.client.Application.createCommandLineDescription(self)
        self.commandline().addGroup("Config");
        self.commandline().addStringOption("Config", "author", "Author of created objects");
        self.commandline().addStringOption("Config", "agency", "Agency of created objects");
        self.commandline().addGroup("Target");
        self.commandline().addStringOption("Target", "event,E", "process the specified event and exit");
        self.commandline().addStringOption("Target", "pick-authors", "space-separated whitelist of pick authors");
        self.commandline().addDoubleOption("Target", "max-residual", "limit the individual pick residual to the specified value (in seconds)");
        self.commandline().addDoubleOption("Target", "max-rms", "limit the pick residual RMS to the specified value (in seconds)");
        self.commandline().addOption("Target", "test", "test mode - don't send the result");


    def init(self):
        if not super(RelocatorApp, self).init():
            return False

        self.inventory = seiscomp.client.Inventory.Instance().inventory()

        return True


    def handleTimeout(self):
        self.kickOffProcessing()


    def addObject(self, parentID, obj):
        # save new object received via messaging
        self.save(obj)


    def updateObject(self, parentID, obj):
        # save updated object received via messaging
        self.save(obj)


    def kickOffProcessing(self):
        # Check for each pending event if it is due to be processed
        for eventID in sorted(self.pendingEvents.keys()):
            if self.readyToProcess(eventID):
                event = self.pendingEvents.pop(eventID)
                self.processEvent(eventID)


    def readyToProcess(self, eventID, minDelay=1200):
        if not eventID in self.pendingEvents:
            seiscomp.logging.error("Missing event "+eventID)
            return False
        evt = self.pendingEvents[eventID]
        preferredOriginID = evt.preferredOriginID()
        if preferredOriginID not in self.origins:
            seiscomp.logging.debug("Loading origin "+preferredOriginID)
            org = scdlpicker.dbutil.loadOrigin(self.query(), preferredOriginID)
            if not org:
                return False
            self.origins[preferredOriginID] = org

        org = self.origins[preferredOriginID]
        now = seiscomp.core.Time.GMT()
        dt = float(now - org.time().value())
        if dt < minDelay:
            return False

        try:
            author = org.creationInfo().author()
        except:
            seiscomp.logging.warning("Author missing in origin %s" % preferredOriginID)
            author = "MISSING"
        ownOrigin = (author == self.author)

        if ownOrigin:
            seiscomp.logging.debug("I made origin "+preferredOriginID+" (nothing to do)")
            del self.pendingEvents[eventID]
            return False

        if not qualified(org):
            seiscomp.logging.debug("Unqualified origin "+preferredOriginID+" rejected")
            del self.pendingEvents[eventID]
            return False

        return True


    def getPicksReferencedByOrigin(self, origin, minWeight=0.5):
        picks = {}
        for i in range(origin.arrivalCount()):
            arr = origin.arrival(i)
            try:
                pickID = arr.pickID()
                if not pickID:
                    continue
                if arr.weight() < minWeight:
                    continue
            except:
                continue
            pick = seiscomp.datamodel.Pick.Find(pickID)
            if not pick:
                continue
            picks[pickID] = pick
        return picks


    def comparePicks(self, origin1, origin2):
        picks1 = self.getPicksReferencedByOrigin(origin1)
        picks2 = self.getPicksReferencedByOrigin(origin2)
        common = {}
        only1 = {}
        only2 = {}

        for pickID in picks1:
            if pickID in picks2:
                common[pickID] = picks1[pickID]
            else:
                only1[pickID] = picks1[pickID]
        for pickID in picks2:
            if pickID not in picks1:
                only2[pickID] = picks2[pickID]

        return common, only1, only2


    def improvement(self, origin1, origin2):
        """
        Test if origin2 is an improvement over origin1.

        This currently only counts picks.
        It doesn't take pick status/authorship into account.
        """
        common, only1, only2 = self.comparePicks(origin1, origin2)
        if len(only2) > len(only1):
            return True
        return False


    def save(self, obj):
        evt = seiscomp.datamodel.Event.Cast(obj)
        if evt:
            seiscomp.logging.debug(evt.publicID())
            if scdlpicker.util.valid(evt):
                self.pendingEvents[evt.publicID()] = evt
            return
        org = seiscomp.datamodel.Origin.Cast(obj)
        if org:
            seiscomp.logging.debug(org.publicID())
            self.origins[org.publicID()] = org
            return


    def loadPicks(self, origin, allowedAuthorIDs):
        etime = origin.time().value()
        elat = origin.latitude().value()
        elon = origin.longitude().value()

        # clear all arrivals
        while origin.arrivalCount():
            origin.removeArrival(0)

        # uses the iasp91 tables by default
        ttt = seiscomp.seismology.TravelTimeTable()

        station = dict()
        for item in scdlpicker.inventory.InventoryIterator(self.inventory, time=etime):
            net, sta, loc, stream = item
            n = net.code()
            s = sta.code()
            if (n,s) in station:
                continue
            station[n,s] = sta

        startTime = origin.time().value()
        endTime = startTime + seiscomp.core.TimeSpan(960.)
        picks = scdlpicker.dbutil.loadPicksForTimespan(self.query(), startTime, endTime)	
        result = []
        for pickID in picks:
            pick = picks[pickID]
            if authorOf(pick) not in allowedAuthorIDs:
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

            maxDelta = 95.

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

        return origin, result


    def processEvent(self, eventID):
        seiscomp.logging.info("Working on event "+eventID)

        event  = scdlpicker.dbutil.loadEvent(self.query(), eventID)
        origin = scdlpicker.dbutil.loadOrigin(self.query(), event.preferredOriginID())

        # adopt fixed depth according to incoming origin
        defaultDepth = 10. # FIXME: no fixed 10 km here
        if scdlpicker.util.hasFixedDepth(origin) and origin.depth().value()==defaultDepth:
            fixed = True
            fixedDepth = origin.depth().value()
            seiscomp.logging.debug("setting fixed depth to %f km" % fixedDepth)
        else:
            fixed = False
            fixedDepth = None
            seiscomp.logging.debug("not fixing depth")

        # Load all picks for a matching time span, independent of association. 
        origin, picks = self.loadPicks(origin, allowedAuthorIDs=["dlpicker"])

        for arr in scdlpicker.util.ArrivalIterator(origin):
            pickID = arr.pickID()
            if not seiscomp.datamodel.Pick.Find(pickID):
                seiscomp.logging.warning("Pick '"+pickID+"' NOT FOUND")

        relocated = scdlpicker.relocation.relocate(origin, eventID, fixedDepth, self.minimumDepth, self.maxResidual)
        if not relocated:
            seiscomp.logging.warning("%s: relocation failed" % eventID)
            return

        now = seiscomp.core.Time.GMT()
        crea = seiscomp.datamodel.CreationInfo()
        crea.setAuthor(self.author)
        crea.setAgencyID(self.agencyID)
        crea.setCreationTime(now)
        crea.setModificationTime(now)
        relocated.setCreationInfo(crea)
        relocated.setEvaluationMode(seiscomp.datamodel.AUTOMATIC)
        self.origins[relocated.publicID()] = relocated

        scdlpicker.util.summarize(relocated)
        if eventID in self.relocated:
            # if quality(relocated) <= quality(self.relocated[eventID]):
            if not self.improvement(self.relocated[eventID], relocated):
                seiscomp.logging.info("%s: no improvement - origin not sent" % eventID)
                return

        ep = seiscomp.datamodel.EventParameters()
        seiscomp.datamodel.Notifier.Enable()
        ep.add(relocated)
        event.add(seiscomp.datamodel.OriginReference(relocated.publicID()))
        msg = seiscomp.datamodel.Notifier.GetMessage()
        seiscomp.datamodel.Notifier.Disable()

        if self.commandline().hasOption("test"):
            seiscomp.logging.info("test mode - not sending "+relocated.publicID())
        else:
            if self.connection().send(msg):
                seiscomp.logging.info("sent "+relocated.publicID())
            else:
                seiscomp.logging.info("failed to send "+relocated.publicID())

        self.relocated[eventID] = relocated


    def run(self):

        seiscomp.datamodel.PublicObject.SetRegistrationEnabled(True)

        try:
            pickAuthors = self.commandline().optionString("pick-authors")
            pickAuthors = pickAuthors.split()
        except RuntimeError:
            pickAuthors = ["dlpicker"]

        try:
            self.author = self.commandline().optionString("author")
        except RuntimeError:
            self.author = "dl-reloc"

        try:
            self.agencyID = self.commandline().optionString("agency")
        except RuntimeError:
            self.agencyID = "GFZ"

        try:
            self.maxResidual = self.commandline().optionDouble("max-residual")
        except RuntimeError:
            self.maxResidual = maxResidual

        try:
            self.maxRMS = self.commandline().optionDouble("max-rms")
        except RuntimeError:
            self.maxRMS = maxRMS

        try:
            eventIDs = self.commandline().optionString("event").split()
        except RuntimeError:
            eventIDs = None

        if eventIDs:
            # immediately process all events and exit
            for eventID in eventIDs:
                self.processEvent(eventID)
            return True

        # enter online mode
        self.enableTimer(1)
        return super(RelocatorApp, self).run()


if __name__ == "__main__":
    app = RelocatorApp(len(sys.argv), sys.argv)
    status = app()
    sys.exit(status)
