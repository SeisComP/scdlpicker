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
"""


import sys, time
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


def author(obj):
    return obj.creationInfo().author()


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
        self.commandline().addGroup("Config");
        self.commandline().addStringOption("Config", "author", "Author of created objects");
        self.commandline().addStringOption("Config", "agency", "Agency of created objects");
        self.commandline().addGroup("Target");
        self.commandline().addStringOption("Target", "event,E",  "load the specified event");
        self.commandline().addStringOption("Target", "pick-authors", "space-separated whitelist of pick authors");
        self.commandline().addDoubleOption("Target", "fixed-depth", "fix the depth at the specified value (in kilometers)");
        self.commandline().addDoubleOption("Target", "max-residual", "limit the individual pick residual to the specified value (in seconds)");
        self.commandline().addDoubleOption("Target", "max-rms", "limit the pick residual RMS to the specified value (in seconds)");
        self.commandline().addOption("Target", "test", "test mode - don't send the result");


    def _load(self, oid, tp):
        assert oid is not None
        obj = self.query().loadObject(tp.TypeInfo(), oid)
        tmp = tp.Cast(obj)
        return tmp


#   def _loadEvent(self, eventID, originIDs=[]):
#       seiscomp.logging.info("Loading event %s from database" % eventID)
#       event = self._load(eventID, seiscomp.datamodel.Event)

#       if not originIDs:
#           originIDs = [ event.preferredOriginID() ]

#       origins = []
#       picks = {}
#       for originID in originIDs:

#           origin = self._load(originID, seiscomp.datamodel.Origin)
#           origins.append(origin)

#           #### Not needed:
#           # self.query().loadArrivals(origin)

#           seiscomp.logging.info("Loading corresponding picks")
#           for arr in scdlpicker.util.ArrivalIterator(origin):
#               pid = arr.pickID()
#               if pid in picks:
#                   continue
#               obj = self._load(pid, seiscomp.datamodel.Pick)
#               picks[pid] = obj
#           seiscomp.logging.info("Loaded %d picks" % len(picks))

#           seiscomp.logging.info("Loading completed")
#       return event, origins, picks


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
            if author(pick) not in authorIDs:
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


    def processEvent(self, eventID):
        seiscomp.logging.info("Working on event "+eventID)

        event  = scdlpicker.dbutil.loadEvent(self.query(), eventID)
        origin = scdlpicker.dbutil.loadOrigin(self.query(), event.preferredOriginID())

        # Load all picks for a matching time span, independent of
        # their association. 
        origin, picks = self.loadPicks(origin, authorIDs=["dlpicker"])

        for arr in scdlpicker.util.ArrivalIterator(origin):
            pickID = arr.pickID()
            if not seiscomp.datamodel.Pick.Find(pickID):
                seiscomp.logging.warning("Pick '"+pickID+"' NOT FOUND")

        relocated = scdlpicker.relocation.relocate(origin, eventID, self.fixedDepth, self.minimumDepth, self.maxResidual)

        now = seiscomp.core.Time.GMT()
        crea = seiscomp.datamodel.CreationInfo()
        crea.setAuthor(self.author)
        crea.setAgencyID(self.agencyID)
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

        if self.commandline().hasOption("test"):
            pass
        else:
            if self.connection().send(msg):
                seiscomp.logging.info("sent "+relocated.publicID())

        scdlpicker.util.summarize(relocated)


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
            self.fixedDepth = self.commandline().optionDouble("fixed-depth")
        except RuntimeError:
            self.fixedDepth = None

        try:
            self.maxResidual = self.commandline().optionDouble("max-residual")
        except RuntimeError:
            self.maxResidual = maxResidual

        try:
            self.maxRMS = self.commandline().optionDouble("max-rms")
        except RuntimeError:
            self.maxRMS = maxRMS

        eventIDs = self.commandline().optionString("event").split()
        for eventID in eventIDs:
            self.processEvent(eventID)

        return True


if __name__ == "__main__":
    app = RelocatorApp(len(sys.argv), sys.argv)
    status = app()
    sys.exit(status)
