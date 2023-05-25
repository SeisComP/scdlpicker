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
import seiscomp.core
import seiscomp.client
import seiscomp.datamodel
import seiscomp.logging
import seiscomp.math
import seiscomp.seismology
import scdlpicker.dbutil as _dbutil
import scdlpicker.util as _util
import scdlpicker.inventory as _inventory
import scdlpicker.relocation as _relocation
import scdlpicker.defaults as _defaults


def quality(origin):
    # similar role of origin score in scautoloc
    return _util.arrivalCount(origin)  # to be improved


class RelocatorApp(seiscomp.client.Application):

    def __init__(self, argc, argv):
        seiscomp.client.Application.__init__(self, argc, argv)
        self.setMessagingEnabled(True)
        self.setDatabaseEnabled(True, True)
        self.setLoadInventoryEnabled(True)
        self.setPrimaryMessagingGroup("LOCATION")
        self.addMessagingSubscription("LOCATION")
        self.addMessagingSubscription("EVENT")

        self.minimumDepth = _defaults.minimumDepth
        self.maxResidual = _defaults.maxResidual

        self.allowedAuthorIDs = _defaults.allowedAuthorIDs

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

        now = seiscomp.core.Time.GMT()
        self._previousPingDB = now


    def createCommandLineDescription(self):
        seiscomp.client.Application.createCommandLineDescription(self)

        self.commandline().addGroup("Config")
        self.commandline().addStringOption(
            "Config", "author", "Author of created objects")
        self.commandline().addStringOption(
            "Config", "agency", "Agency of created objects")

        self.commandline().addGroup("Target")
        self.commandline().addStringOption(
            "Target", "event,E", "process the specified event and exit")
        self.commandline().addStringOption(
            "Target", "pick-authors",
            "space-separated whitelist of pick authors")
        self.commandline().addDoubleOption(
            "Target", "max-residual",
            "limit the individual pick residual to the specified value "
            "(in seconds)")
        self.commandline().addDoubleOption(
            "Target", "max-rms",
            "limit the pick residual RMS to the specified value (in seconds)")
        self.commandline().addOption(
            "Target", "test", "test mode - don't send the result")

    def init(self):
        if not super(RelocatorApp, self).init():
            return False

        self.inventory = seiscomp.client.Inventory.Instance().inventory()

        return True

    def pingDB(self):
        """
        Keep the DB connection alive by making a dummy request every minute
        """
        now = seiscomp.core.Time.GMT()
        if float(now - self._previousPingDB) > 60:
            self.query().getObject(
                seiscomp.datamodel.Event.TypeInfo(), "dummy")
            self._previousPingDB = now

    def handleTimeout(self):
        self.pingDB()
        self.kickOffProcessing()

    def addObject(self, parentID, obj):
        # Save new object received via messaging. The actual processing is
        # started from handleTimeout().
        self.save(obj)

    def updateObject(self, parentID, obj):
        # Save new object received via messaging. The actual processing is
        # started from handleTimeout().
        self.save(obj)

    def save(self, obj):
        # Save object for later processing in handleTimeout()
        evt = seiscomp.datamodel.Event.Cast(obj)
        if evt:
            seiscomp.logging.debug("Saving "+evt.publicID())
            if _util.valid(evt):
                self.pendingEvents[evt.publicID()] = evt
            return evt
        org = seiscomp.datamodel.Origin.Cast(obj)
        if org:
            seiscomp.logging.debug("Saving "+org.publicID())
            self.origins[org.publicID()] = org
            return org

    def kickOffProcessing(self):
        # Check for each pending event if it is due to be processed
        for eventID in sorted(self.pendingEvents.keys()):
#           seiscomp.logging.debug("kickOffProcessing begin " + eventID)
            if self.readyToProcess(eventID):
                self.pendingEvents.pop(eventID)
                self.processEvent(eventID)
#           seiscomp.logging.debug("kickOffProcessing   end " + eventID)

    def readyToProcess(self, eventID, minDelay=1080):
        if eventID not in self.pendingEvents:
            seiscomp.logging.error("Missing event "+eventID)
            return False
        evt = self.pendingEvents[eventID]
        preferredOriginID = evt.preferredOriginID()
        if preferredOriginID not in self.origins:
            seiscomp.logging.debug("Loading origin "+preferredOriginID)
            org = _dbutil.loadOriginWithoutArrivals(
                self.query(), preferredOriginID)
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
        except Exception:
            seiscomp.logging.warning(
                "Author missing in origin %s" % preferredOriginID)
            author = "MISSING"
        ownOrigin = (author == self.author)

        if ownOrigin:
            seiscomp.logging.debug(
                "I made origin "+preferredOriginID+" (nothing to do)")
            del self.pendingEvents[eventID]
            return False

        if not _util.qualified(org):
            seiscomp.logging.debug(
                "Unqualified origin "+preferredOriginID+" rejected")
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
            except Exception:
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
        count1 = len(only1) + len(common)
        count2 = len(only2) + len(common)

        seiscomp.logging.debug("count %4d ->%4d" % (count1, count2))

        try:
            rms1 = max(origin1.quality().standardError(), 1.)
        except ValueError:
            rms1 = 10.  # FIXME hotfix

        try:
            rms2 = max(origin2.quality().standardError(), 1.)
        except ValueError:
            seiscomp.logging.debug("origin2 without standardError")
            rms2 = 1.

        seiscomp.logging.debug("count %4d ->%4d" % (count1, count2))
        seiscomp.logging.debug("rms   %4.1f ->%4.1f" % (rms1, rms2))

        if count1 == 0:
            return True

        q = (count2/count1)**2 * (rms1/rms2)
        seiscomp.logging.debug("improvement  %.3f" % q)

        return q > 1

    def processEvent(self, eventID):
        event = _dbutil.loadEvent(self.query(), eventID)
        if not event:
            seiscomp.logging.warning("Failed to load event " + eventID)
            return

        seiscomp.logging.debug("Loaded event "+eventID)
        origin = _dbutil.loadOriginWithoutArrivals(
            self.query(), event.preferredOriginID())
        seiscomp.logging.debug("Loaded origin " + origin.publicID())

        # adopt fixed depth according to incoming origin
        defaultDepth = 10.  # FIXME: no fixed 10 km here
        if _util.hasFixedDepth(origin) \
                and origin.depth().value() == defaultDepth:
            # fixed = True
            fixedDepth = origin.depth().value()
            seiscomp.logging.debug("setting fixed depth to %f km" % fixedDepth)
        else:
            # fixed = False
            fixedDepth = None
            seiscomp.logging.debug("not fixing depth")

        # Load all picks for a matching time span, independent of association.
        originWithArrivals, picks = \
            _dbutil.loadPicksForOrigin(
                origin, self.inventory, self.allowedAuthorIDs, self.query())
        seiscomp.logging.debug(
            "arrivalCount=%d" % originWithArrivals.arrivalCount())

        relocated = _relocation.relocate(
            originWithArrivals, eventID, fixedDepth,
            self.minimumDepth, self.maxResidual)
        if not relocated:
            seiscomp.logging.warning("%s: relocation failed" % eventID)
            return
        if relocated.arrivalCount() < 5:
            seiscomp.logging.info("%s: too few arrivals" % eventID)
            return

        now = seiscomp.core.Time.GMT()
        ci = _util.creationInfo(self.author, self.agencyID, now)
        relocated.setCreationInfo(ci)
        relocated.setEvaluationMode(seiscomp.datamodel.AUTOMATIC)
        self.origins[relocated.publicID()] = relocated

        _util.summarize(relocated)
        if eventID in self.relocated:
            # if quality(relocated) <= quality(self.relocated[eventID]):
            if not self.improvement(self.relocated[eventID], relocated):
                seiscomp.logging.info(
                    "%s: no improvement - origin not sent" % eventID)
                return

        ep = seiscomp.datamodel.EventParameters()
        seiscomp.datamodel.Notifier.Enable()
        ep.add(relocated)
        event.add(seiscomp.datamodel.OriginReference(relocated.publicID()))
        msg = seiscomp.datamodel.Notifier.GetMessage()
        seiscomp.datamodel.Notifier.Disable()

        if self.commandline().hasOption("test"):
            seiscomp.logging.info(
                "test mode - not sending " + relocated.publicID())
        else:
            if self.connection().send(msg):
                seiscomp.logging.info("sent " + relocated.publicID())
            else:
                seiscomp.logging.info("failed to send " + relocated.publicID())

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
            self.maxResidual = _defaults.maxResidual

        try:
            self.maxRMS = self.commandline().optionDouble("max-rms")
        except RuntimeError:
            self.maxRMS = _defaults.maxRMS

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
