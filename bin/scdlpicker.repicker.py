#!/usr/bin/env seiscomp-python
# -*- coding: utf-8 -*-
###########################################################################
# Copyright (C) GFZ Potsdam                                               #
# All rights reserved.                                                    #
#                                                                         #
# Authors:                                                                #
#     Thomas Bornstein                                                    #
#     Joachim Saul (saul@gfz-potsdam.de)                                  #
#                                                                         #
# GNU Affero General Public License Usage                                 #
# This file may be used under the terms of the GNU Affero                 #
# Public License version 3.0 as published by the Free Software Foundation #
# and appearing in the file LICENSE included in the packaging of this     #
# file. Please review the following information to ensure the GNU Affero  #
# Public License version 3.0 requirements will be met:                    #
# https://www.gnu.org/licenses/agpl-3.0.html.                             #
###########################################################################

import pathlib
import sys
import yaml
import time
import numpy as np
import scipy.signal
from typing import Tuple
import argparse
import obspy

import seiscomp.core
import seiscomp.client
import seiscomp.datamodel
import seiscomp.logging

import scdlpicker.config as _config


# Here is the place to import other DL models
from seisbench.models import EQTransformer, PhaseNet

models = {
    'phasenet': PhaseNet,
    'eqtransformer': EQTransformer,

    # short names for convenience
    'phn': PhaseNet,
    'eqt': EQTransformer,
}


class EventWorkspaceContainer:

    def __init__(self):
        self.picks = dict()
        self.mlpicks = dict()
        self.waveforms = dict()


Pick = obspy.core.AttribDict


def dotted_nslc(pick):
    return "%s.%s.%s.%s" % (
        pick.networkCode,
        pick.stationCode,
        pick.locationCode,
        pick.channelCode )


def timestamp(t):
    return (t.isoformat() + "000000")[:23] + "Z"


class RepickerApp(seiscomp.client.Application):
    """A class to hold settings and provide methods for arbitrary seisbench
    compatible P wave pickers.

    Besides using the chosen ML model (by instanciating one of the inherited
    repicker classes) to repick the input stream, it also does preparations
    as collecting adhoc picks, constructing a stream depending on the chosen
    batch size, doing sanity checks, as well as post-processing like writing
    out annotations and picks to be further processed bei seiscomp.

    The Repicker is run in an infinity loop polling for new symbolic links
    inside the sub-directory `spool/`. Those links should point to YAML files
    containing picks which are usually residing in an `events/<EVENT>/`
    sub-folder.

    The refined picks are written into YAML files inside `outgoing/`.

    Args:
        workingDir(str): Directory containing input, output and waveform files.

        test(bool): To test the main functionality, stops before writing
                    outgoing files

        batchSize(int): Repicker will set the size of a batch to this
                        (at maximum)

        device(str): Defines where to run the model - "cpu" or "gpu"
    """

    def __init__(self, argc, argv):
        argv = argv.copy()
        argv[0] = "scdlpicker"

        super().__init__(argc, argv)
        self.setDatabaseEnabled(False, False)
        self.setLoadInventoryEnabled(False)
        self.setLoadConfigModuleEnabled(False)
        self.setMessagingEnabled(False)

        self.test = False
        self.singleRun = False

    def createCommandLineDescription(self):
        super().createCommandLineDescription()

        self.commandline().addGroup("Config")
        self.commandline().addStringOption(
            "Config", "working-dir,d", "Path of the working directory where intermediate files are placed and exchanged")
        self.commandline().addStringOption(
            "Config", "device", "'cpu' or 'gpu'. Default is 'cpu' but with access to a cuda device you can change this parameter to 'gpu'")
        self.commandline().addStringOption(
            "Config", "model", "Choose one of the available ML models to make the predictions")
        self.commandline().addStringOption(
            "Config", "dataset", "The dataset on which the model was predicted")
        self.commandline().addStringOption(
            "Config", "batch-size", "Set batch size. Should be suitable for the hardware used [50]")
        self.commandline().addStringOption(
            "Config", "min-confidence", "Confidence threshold below which a pick is skipped")

        self.commandline().addGroup("Mode")
        self.commandline().addStringOption(
            "Mode", "test", "Test mode - don't write outgoing parameter file with refined picks")
        self.commandline().addStringOption(
            "Mode", "exit", "Exit after all items in spool folder have been processed")

        return True

    def init(self):
        if not super(RepickerApp, self).init():
            return False

        self.commonConfig = _config.getCommonConfig(self)
        self.workingDir = self.commonConfig.workingDir

        self.pickingConfig = _config.getPickingConfig(self)

        if self.pickingConfig.modelName not in models:
            raise ValueError("No such model: " + self.pickingConfig.modelName)

        self.model = models[self.pickingConfig.modelName].from_pretrained(self.pickingConfig.dataset)

        self.eventRootDir = self.workingDir / "events"
        self.spoolDir = self.workingDir / "spool"
        self.workspaces = dict()

        if self.commonConfig.device == "cpu":
            self.model.cpu()
        elif self.commonConfig.device == "gpu":
            self.model.cuda()
        else:
            seiscomp.logging.error("Unknown device " + self.commonConfig.device)
            return False

        self.expected_input_length_sec = \
            self.model.in_samples / self.model.sampling_rate

        return True

    def dumpConfiguration(self):
        info = seiscomp.logging.info

        info("Global parameters")
        info("  agency = " + self.agencyID())
        info("  author = " + self.author())
        self.commonConfig.dump(info)
        self.pickingConfig.dump(info)

    def _get_stream_from_picks(self, picks, eventID) \
            -> Tuple[obspy.core.stream.Stream, list]:
        """
        For the given picks, read the corresponding streams and return them.

        No streams are returned if at least one component is missing. Since
        not always for all picks all streams will be complete, a list of picks
        for which we have all data is returned.
        """

        # Collect streams from mseed files
        collected_picks = []
        stream = obspy.core.stream.Stream()
        for pick in picks:
            pick: Pick
            pickID = pick.publicID
            seiscomp.logging.debug("//// " + pickID)

            nslc = (pick.networkCode, pick.stationCode,
                    pick.locationCode, pick.channelCode[0:2])
            nslc = "%s.%s.%s.%s" % nslc

            # Create an obspy stream from according mseed files

            waveformsDir = self.eventRootDir / eventID / "waveforms"

            files = []
            for components in [("Z",), ("N", "1"), ("E", "2")]:
                for c in components:
                    file = waveformsDir / (nslc + c + ".mseed")
                    if file.exists():
                        break
                else:
                    file = None
                if file:
                    files.append(file)

            if len(files) < 3:
                seiscomp.logging.debug("---- " + pickID)
                if not files:
                    seiscomp.logging.debug("---- not enough data -> skipped")
                else:
                    seiscomp.logging.debug("---- missing components -> skipped")
                continue

            # All needed files exist
            seiscomp.logging.debug("++++ " + pickID)

            streams = []

            # We try to open all three component files and if we fail on
            # any of these we give up.
            try:
                streams = [obspy.core.stream.read(f) for f in files]
            # The following are all real-life exceptions observed in the past
            # and which we tolerate for the time being.
            except (TypeError, ValueError,
                    obspy.io.mseed.InternalMSEEDError,
                    obspy.io.mseed.ObsPyMSEEDFilesizeTooSmallError) as e:
                seiscomp.logging.warning(
                    "Caught " + repr(e) + " while processing pick " + pickID)
                continue
            except Exception as e:
                seiscomp.logging.warning("Unknown exception: " + str(e))

            # Check if trace is shorter than needed
            for s in streams:
                trace_len = s[0].stats.endtime - s[0].stats.starttime
                if trace_len < self.expected_input_length_sec:
                    seiscomp.logging.warning(
                        f"Trace {nslc} ({s[0].meta.channel}): "
                        "length {trace_len:.2f}s is too short. "
                        "Picker needs {self.expected_input_length_sec:.2f}s.")
                    break
            else:
                for s in streams:
                    stream += s
                collected_picks.append(pick)

        if len(collected_picks) == 0:
            seiscomp.logging.debug(f"Empty stream for event {eventID}.")

        if len(stream) == 0:
            stream = None

        return stream, collected_picks

    def _process(self, adhoc_picks, eventID):
        """
        Looks for new picks among the passed adhoc_picks, passes them
        to _ml_predict(), adds the new predictions to the event
        workspace and returns all recently calculated ML picks.
        """

        seiscomp.logging.debug("process %s    %d picks" % (eventID, len(adhoc_picks)))

        if eventID not in self.workspaces:
            self.workspaces[eventID] = EventWorkspaceContainer()
        workspace = self.workspaces[eventID]

        # Retrieve additional picks from ep
        new_adhoc_picks = []
        for pick in adhoc_picks:
            pick_id = pick.publicID
            # We need to avoid to repeatedly try to process picks
            # we have already finished processing. So we only process
            # picks that we haven't seen for this event. This is OK
            # because there is no cross-talk between picks. However,
            # in case we analyze picks in the context of other
            # picks, we will have to re-process previously processed
            # picks. Then this filtering will not be appropriate.

            # if pickID not in workspace.mlpicks:
            if pick_id not in workspace.picks:
                workspace.picks[pick_id] = pick
                new_adhoc_picks.append(pick)
        tmp = "%d" % len(new_adhoc_picks) if new_adhoc_picks else "no"
        seiscomp.logging.debug(tmp + " new picks")

        if not new_adhoc_picks:
            return []

        # Extra debug output to see if we accidentally process
        # any "new" picks twice
        for pick in new_adhoc_picks:
            seiscomp.logging.debug("NEW PICK %s" % pick.publicID)

        # ++++++++++++ Get Predictions +++++++++++++++++#
        predictions = self._ml_predict(new_adhoc_picks, eventID)

        if not predictions:
            seiscomp.logging.warning("processing returned without result")
            return []

        new_picks = list()

        for pick_id in predictions:
            # In order to accommodate more than one maximum exceeding
            # the threshold, this is now a list of (time, confidence)
            # pairs.
            preds = predictions[pick_id]

            triggering_pick = workspace.picks[pick_id]

            for (ml_time, ml_conf) in preds:
                seiscomp.logging.info("PICK   %s" % pick_id)
                seiscomp.logging.info("RESULT %s  c= %.2f" %
                            (timestamp(ml_time), ml_conf))

                # FIXME: temporary criterion
                # On one hand we want as small a time window as possible, but
                # on the other hand it must be large enough to accommodate
                # residuals due to wrong source depth.
                # TODO: iterate!
                dt_max = 10
                dt = abs(ml_time - obspy.UTCDateTime(triggering_pick.time))
                if abs(dt) > dt_max:
                    seiscomp.logging.info("SKIPPED dt = %.2f" % dt)
                    continue
                if ml_conf < self.pickingConfig.minConfidence:
                    seiscomp.logging.info("SKIPPED conf = %.3f" % ml_conf)
                    continue
                old_pick = workspace.picks[pick_id]
                new_pick = old_pick.copy()
                new_pick.publicID = old_pick.publicID + "/repick"
                new_pick.model = self.pickingConfig.modelName
                new_pick.confidence = float("%.3f" % ml_conf)
                new_pick.time = timestamp(ml_time)

                # The key of the ML pick is the publicID of the
                # original pick in order to make association easier.
                # This will later be relevant for relocation, where
                # we will actually replace existing picks with their
                # ML equivalent.
                workspace.mlpicks[pick_id] = new_pick

                # FIXME: For the time being
                assert new_pick not in new_picks
                new_picks.append(new_pick)

        return new_picks

    def _spoolItems(self):
        d = self.spoolDir
        filenames = [i for i in d.glob("*.yaml")]
        items = list()
        for path in sorted(filenames):
            if path.is_symlink():
                target = path.readlink()
                if not target.exists():
                    seiscomp.logging.warning("missing %s" % target)
                    continue

                items.append( (path, target) )
        return items

    def _readPicksFromYaml(self, yamlFileName):
        with open(yamlFileName) as f:
            streamIDs = []
            picks = list()
            for p in yaml.safe_load(f):
                # Prevent duplicate stream IDs
                streamID = p["streamID"]
                duplicateStreamID = streamID in streamIDs
                if duplicateStreamID:
                    continue
                streamIDs.append(streamID)
                pick = Pick(p)
                try:
                    pick.phaseHint
                except AttributeError:
                    pick.phaseHint = "P"
                picks.append(pick)
            return picks

    def _writePicksToYaml(self, picks, yamlFileName):
        tmp = [ dict(pick) for pick in picks ]
        with open(yamlFileName, 'w') as f:
            yaml.dump(tmp, f)

    def _poll(self, reverse=True):
        """
        Check whether there is data waiting to be processed.

        * Is there a new symlink in the spool directory?
        * If yes:
            - follow the symlink
            - read the event parameters
            - process the event parameters
            - if successful and not in test mode, remove symlink
        """
        self.spoolDir.mkdir(parents=True, exist_ok=True)

        # We prioritize the last-added spool items.
        #
        # This is good if after a long outage we want to be in
        # real-time mode quickly. But on the other hand the most
        # recent items are also usually the biggest and take
        # longest. Need to test if that has no unwanted side
        # effects. Possibly slight delays in real-time mode as
        # bigger items are prioritized, which take longer to
        # process. Or we divide big items into smaller ones. TBD
        for item in sorted(self._spoolItems(), reverse=reverse):
            link, target = item

            seiscomp.logging.debug("+++reading %s" % target)
            adhoc_picks = self._readPicksFromYaml(target)

            # FIXME: hackish
            # The input yaml path name is composed of
            # /some/folder/name/eventID/in/oneOutOfMany.yaml
            # so the eventID is always at a fixed position in the
            # path. This is required.
            assert str(target).endswith(".yaml")
            eventID = str(target).split("/")[-3]

            try:
                new_picks = self._process(adhoc_picks, eventID)
            except RuntimeError as e:
                seiscomp.logging.warning(str(e))
                continue

            if not new_picks:
                seiscomp.logging.info("no results - exiting")
                link.unlink()
                continue

            if self.test:
                seiscomp.logging.info("+++test mode - stopping")
                continue

            eventDir = self.eventRootDir / eventID
            # directory to which we write the resulting yaml files
            outDir = eventDir / "out"
            outDir.mkdir(parents=True, exist_ok=True)
            yamlFileName = outDir / link.name
            self._writePicksToYaml(new_picks, yamlFileName)

            outgoingDir = self.workingDir / "outgoing"
            outgoingDir.mkdir(parents=True, exist_ok=True)

            dst = outgoingDir / yamlFileName.name
            src = yamlFileName

            try:
                seiscomp.logging.debug("creating symlink %s -> %s" % (dst, src))
                dst.symlink_to(src)
            except FileExistsError:
                seiscomp.logging.warning("symlink  %s -> %s" % (dst, src))

            # we are done with this item
            link.unlink()

            # If in reverse mode, break after first processed item
            # in order to check if there are new items, which will
            # then also be processed first. If not in reverse mode
            # we don't care.
            if reverse:
                break

    def run(self):
        """Main loop"""

        self.dumpConfiguration()

        while True:
            self._poll()
            time.sleep(1)

            if self.singleRun:
                seiscomp.logging.info("+++single-run mode - exiting")
                break

        return True

    def fill_result(self, predictions, stream, collected_picks,
                    annotDir, eventID):
        """Fills `predictions` with annotations done by the model
           using the stream. Additional data will be taken from
           `collected_picks`.
        """
        annotations, assoc_ind = None, None
        try:

            # ************ Model call ****************#
            annotations = self.model.annotate(stream)

            # Only use those predictions that were done for P wave onsets
            annotations = list(filter(
                lambda a: a.id.split('.')[-1].endswith('_P'), annotations))

            # indexes list of successfully associated annotations
            assoc_ind = []
            for i, annotation in enumerate(annotations):
                try:
                    # Associate the annotation to a Pick

                    pick = next(filter(
                        lambda p:
                        p.networkCode == annotation.meta.network
                        and p.stationCode == annotation.meta.station
                        and p.locationCode == annotation.meta.location,
                        collected_picks))
                except StopIteration:
                    seiscomp.logging.warning(
                        "%s: failed to associate annotation for %s.%s" % (
                            eventID,
                            annotation.meta.network,
                            annotation.meta.station))

                    # No Pick could be found that matches the
                    # current annotation. The reason for this could be
                    # a gap in waveform data such that two traces of
                    # the same stations are passed to the model
                    # therefore the model predicts a second time, but
                    # since no Pick is waiting for it, this
                    # prediction will be discarded. This problem should
                    # be addressed in future versions by providing clean
                    # data, beforehand, because it would be too difficult
                    # to decide right here which pick is the better one
                    # resp. the one wanted.
                    continue

                assoc_ind.append(i)
                annot_f = annotDir / (dotted_nslc(pick) + ".sac")
                annotation.write(str(annot_f), format="SAC")

                confidence = annotation.data.astype(np.double)
                times = annotation.times()

                # The required min. distance between peaks is one second,
                # i.e. the sampling rate controls the number of samples.
                peaks, _ = scipy.signal.find_peaks(
                    confidence, height=0.1, distance=self.model.sampling_rate)
                for peak in peaks:
                    picktime = annotation.stats.starttime + times[peak]
                    if pick.publicID not in predictions:
                        predictions[pick.publicID] = []
                    new_item = (picktime, confidence[peak])
                    predictions[pick.publicID].append(new_item)
                    seiscomp.logging.debug(
                        "#### " + pick.publicID + "  %.3f" % confidence[peak])

                collected_picks.remove(pick)

        except (TypeError, ValueError, ZeroDivisionError) as e:
            seiscomp.logging.error(eventID+": caught "+repr(e))

        if None not in [annotations, assoc_ind]:
            # Clean annotations from those who were associated successfully
            [annotations.pop(i) for i in sorted(assoc_ind, reverse=True)]

            left_annos_n = len(annotations)
            left_adhocs_n = len(collected_picks)
            if left_annos_n > 0:
                seiscomp.logging.warning(
                    f"There were {left_annos_n} annotations that "
                    "could not be associated.")
            if left_adhocs_n > 0:
                seiscomp.logging.warning(
                    f"There were {left_adhocs_n} picks for "
                    "which no annotation was done.")

    def _ml_predict(self, adhoc_picks, eventID):
        """
        Based on a list of picks, perform repicking and fill a dict with
        the predictions, each a (Time, confidence) pair, and returns it.

        Returns:
            dict: a dictionary of `pickID: (time, confidence)` pairs
        """

        seiscomp.logging.debug("Starting prediction")

        annotDir = self.eventRootDir / eventID / "annot"
        annotDir.mkdir(parents=True, exist_ok=True)

        acc_predictions = {}
        picks_remain_size = picks_all_size = len(adhoc_picks)
        start_index, end_index = 0, min(self.pickingConfig.batchSize, picks_all_size)

        # Batch loop
        # We process the input data in batches with the size defined
        # by batchSize
        while picks_remain_size > 0:
            seiscomp.logging.debug(f"ML prediction {picks_remain_size} remaining picks")
            picks_batch = adhoc_picks[start_index:end_index]

            try:
                stream, collected_picks = \
                    self._get_stream_from_picks(picks_batch, eventID)
            except Exception as e:
                etxt = str(e)
                seiscomp.logging.debug(f"{eventID}: caught unknown exception: {etxt}")
                stream = None

            if stream is not None:
                # In some cases no picks are returned, nonetheless this
                # could be true for the current batch of picks only, the
                # next batch could be ok, therefore we just need to pass
                # the following line
                self.fill_result(
                    acc_predictions, stream, collected_picks, annotDir, eventID)

            # Prepare for next batch
            picks_remain_size -= self.pickingConfig.batchSize
            start_index += self.pickingConfig.batchSize
            end_index = min(
                start_index + self.pickingConfig.batchSize,
                start_index + picks_remain_size)

        seiscomp.logging.debug("Finished prediction.")
        return acc_predictions


def main():
    app = RepickerApp(len(sys.argv), sys.argv)
    app()


if __name__ == "__main__":
    main()
