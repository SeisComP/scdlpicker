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

import os
import yaml
import time
import logging
import numpy as np
import scipy.signal
from typing import Tuple
import argparse
import obspy
from abc import ABC, abstractmethod

# Here is the place to import other DL models
from seisbench.models import EQTransformer, PhaseNet

# TEPSW stands for TEP with Sliding Window is an older version of TEP
# The most recent version is simply called TEP
try:
    from tep.tep import TEPSW, TEP
except ImportError:
    TEPSW = TEP = None

LOGFORMAT = "%(levelname)-8s  %(asctime)s %(message)s"
logging.basicConfig(format=LOGFORMAT)
logger = logging.getLogger('origin-repicker')
logger.setLevel(logging.DEBUG)


class EventWorkspaceContainer:
    # TODO AFAICS this is a short version of the one in eventworkspace.py,
    #      would recommend to make it even shorter.
    # TODO And to make the project more consistent, I renamed the class

    def __init__(self):
        self.picks = dict()
        self.mlpicks = dict()
        self.waveforms = dict()


class AdHocPick:
    """
    An AdHocPick object is a pick with a publicID that the
    DL model refines by repicking.
    """

    def __init__(self, public_id):
        self.channelCode = None
        self.locationCode = None
        self.stationCode = None
        self.networkCode = None
        self.publicID = public_id

    def __str__(self):
        """ Implemented for debugging purposes. """
        return self.publicID

    def clone(self):
        clone = AdHocPick(self.publicID)
        clone.networkCode = self.networkCode
        clone.stationCode = self.stationCode
        clone.locationCode = self.locationCode
        clone.channelCode = self.channelCode
        clone.time = self.time
        return clone


class Repicker(ABC):
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
        eventRootDir(str): Directory containing event folders, used to
                           store annotations.

        spoolDir(str): Directory to watch for links to yaml files

        test(bool): To test the main functionality, stops before writing
                    outgoing files

        batchSize(int): Repicker will set the size of a batch to this
                        (at maximum)

        device(str): Defines where to run the model - "cpu" or "gpu"
    """

    def __init__(self, workingDir=".", eventRootDir="events", spoolDir="spool",
                 test=False, exit=False, batchSize=False, device="cpu",
                 minConfidence=None, annotDir="annot"):

        self.test = test
        self.exit = exit
        self.workingDir = workingDir
        self.eventRootDir = eventRootDir
        self.spoolDir = spoolDir
        self.batchSize = batchSize
        self.workspaces = dict()
        self.minConfidence = minConfidence
        self.annotDir = annotDir

        if device == "cpu":
            self.model.cpu()
        elif device == "gpu":
            self.model.cuda()

        self.expected_input_length_sec = self.model.in_samples / self.model.sampling_rate

    @property
    @abstractmethod
    def model(self):
        pass

    @model.setter
    def model(self, model):
        self.model_ = model

    @model.getter
    def model(self):
        return self.model_

    def _get_stream_from_picks(
            self,
            picks,
            eventID) -> Tuple[str, obspy.core.stream.Stream, list]:
        """ Fills

        """
        logger.info("################ get_stream_from_picks %d new picks" % len(picks))

        # Collect streams from mseed files
        collected_ahoc_picks = []
        stream = None
        eventRootDir = self.eventRootDir
        for pick in picks:
            pick: AdHocPick
            pickID = pick.publicID
            logger.debug("//// " + pickID)

            nslc = (pick.networkCode, pick.stationCode,
                    pick.locationCode, pick.channelCode[0:2])
            nslc = "%s.%s.%s.%s" % nslc

            # Create an obspy stream from according mseed files

            waveforms_d = os.path.join(eventRootDir, eventID, "waveforms")

            fileZ = os.path.join(waveforms_d, nslc + "Z" + ".mseed")
            if not os.path.exists(fileZ):
                fileZ = None

            for component in ["N", "1"]:
                fileN = os.path.join(waveforms_d, nslc + component + '.mseed')
                if os.path.exists(fileN):
                    break
            else:
                fileN = None

            for component in ["E", "2"]:
                fileE = os.path.join(waveforms_d, nslc + component + '.mseed')
                if os.path.exists(fileE):
                    break
            else:
                fileE = None

            if not fileZ and not fileN and not fileE:
                # no data at all -> no debug message needed
                logger.debug("---- " + pickID)
                logger.debug("---- no data -> skipped")
                continue
            if not fileZ or not fileN or not fileE:
                # partly missing data
                logger.debug("---- " + pickID)
                logger.debug("---- missing components -> skipped")
                continue

            logger.debug("++++ " + pickID)

            streamZ, streamE, streamN = None, None, None

            # We try to open all three component files and if we fail on
            # any of these we give up.
            try:
                streamZ = obspy.core.stream.read(fileZ)
                streamN = obspy.core.stream.read(fileN)
                streamE = obspy.core.stream.read(fileE)
            # The following are all real-life exceptions observed in the past
            # and which we tolerate for the time being.
            except (TypeError, ValueError,
                obspy.io.mseed.InternalMSEEDError,
                obspy.io.mseed.ObsPyMSEEDFilesizeTooSmallError) as e:
                logger.warning(
                    "Caught " + repr(e) + " while processing pick " + pickID)
                continue
            except Exception as e:
                logger.warning("Unknown exception: " + str(e))

            if None in (streamZ, streamE, streamE):
                logger.warning(
                    f" {nslc}: Didn't find mseed files for all components.")
                continue
            else:
                [ _.merge(method=1, fill_value=0, interpolation_samples=0)
                    for _ in [streamZ, streamE, streamN]]
            if not stream:
                stream = obspy.core.stream.Stream()

            # Check if trace is shorter than needed
            for t in (streamZ, streamE, streamN):
                trace_len = t[0].stats.endtime - t[0].stats.starttime
                if trace_len < self.expected_input_length_sec:
                    logger.warning(
                        f"Trace {nslc} ({t[0].meta.channel}): "
                        "length {trace_len:.2f}s is too short. "
                        "Picker needs {self.expected_input_length_sec:.2f}s.")
                    break
            else:
                stream += streamZ
                stream += streamN
                stream += streamE
                collected_ahoc_picks.append(pick)
        if len(collected_ahoc_picks) == 0:
            logger.debug(f"Empty stream for event {eventID}.")
        logger.debug("################ get_stream_from_picks end")
        return eventID, stream, collected_ahoc_picks

    def _process(self, adhoc_picks, eventID):
        """
        Looks for new picks among the passed adhoc_picks, passes them
        to _ml_predict(), adds the new predictions to the event
        workspace and returns all recently calculated ML picks.
        """

        logger.debug("process %s    %d picks" % (eventID, len(adhoc_picks)))

        if eventID not in self.workspaces:
            self.workspaces[eventID] = EventWorkspaceContainer()
        workspace = self.workspaces[eventID]

        # retrieve additional picks from ep
        new_adhoc_picks = []
        for pick in adhoc_picks:
            pick_id = pick.publicID
            # We need to avoid to again and again try to process
            # picks we have already worked on. So we only process
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
        logger.debug(tmp + " new picks")

        if not new_adhoc_picks:
            return []

        # extra debug output to see if we accidentally process
        # any "new" picks twice
        for pick in new_adhoc_picks:
            logger.debug("NEW PICK %s" % pick.publicID)

        # ++++++++++++ Get Predictions +++++++++++++++++#
        predictions = self._ml_predict(new_adhoc_picks, eventID)

        if not predictions:
            logger.warning("tep_predict returned without result")
            return []

        new_picks = list()

        for pick_id in predictions:
            # In order to accommodate more than one maximum exceeding
            # the threshold, this is now a list of (time, confidence)
            # pairs.
            preds = predictions[pick_id]

            triggering_pick = workspace.picks[pick_id]

            for (ml_time, ml_conf) in preds:
                ml_timestamp = (ml_time.isoformat() + "000000")[:23] + "Z"
                logger.info("PICK   %s" % pick_id)
                logger.info("RESULT %s  c= %.2f" % (ml_timestamp, ml_conf))

                # FIXME: temporary criterion
                # On one hand we want as small a time window as
                # possible, but on the other hand it must be large
                # enough to accommodate large due to wrong source depth.
                # TODO: iteration!
                dt_max = 10
                dt = abs(ml_time - obspy.UTCDateTime(triggering_pick.time))
                if abs(dt) > dt_max:
                    logger.info("SKIPPED dt = %.2f" % dt)
                    continue
                if ml_conf < self.minConfidence:
                    logger.info("SKIPPED conf = %.3f" % ml_conf)
                    continue
                old_pick = workspace.picks[pick_id]
                new_pick = old_pick.clone()
                new_pick.publicID = old_pick.publicID + "/repick"
                new_pick.confidence = ml_conf
                new_pick.time = ml_timestamp
                # FIXME: new_pick.time is an isotimestamp without uncertainties

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


    def _poll(self, reverse=True):
        """
        Check whether there is data ready to be processed.

        * Is there a new symlink in the spool directory?
        * If yes:
            - follow the symlink
            - read the event parameters
            - process the event parameters
            - if successful and not in test mode, remove symlink
        """
        try:
            os.makedirs(self.spoolDir)
        except FileExistsError:
            pass

        items = [i for i in os.listdir(self.spoolDir)
                 if i.endswith(".yaml")]

        todolist = list()

        for item in sorted(items):
            path = os.path.join(self.spoolDir, item)

            if os.path.islink(path):
                target = os.readlink(path)
                target = os.path.join(self.spoolDir, target)
                if not os.path.exists(target):
                    logger.warning("missing " + target)
                    continue

                todoitem = (path, target)
                todolist.append(todoitem)

        # By reversing the todolist, we prioritize the last-added
        # items. This is good if after a long outage we want to be
        # in real-time mode quickly. But on the other hand the most
        # recent items are also usually the biggest and take
        # longest. Need to test if that has no unwanted side
        # effects. Possibly slight delays in real-time mode as
        # bigger items are prioritized, which take longer to
        # process. Or we divide big items into smaller ones. TBD
        for todoitem in sorted(todolist, reverse=reverse):
            link, target = todoitem

            logger.debug("+++reading %s" % target)

            # FIXME: hackish
            # The input yaml path name is composed of
            # /some/folder/name/eventID/in/oneOutOfMany.yaml
            # so the eventID is always at a fixed position in the
            # path. This is required.
            assert target.endswith(".yaml")
#           assert target.split("/")[-4] == self.eventRootDir
            eventID = target.split("/")[-3]

            eventDir = os.path.join(self.eventRootDir, eventID)

            streamIDs = []
            with open(target) as yf:
                adhoc_picks = list()
                for p in yaml.safe_load(yf):
                    # Make sure that we process only
                    # one pick per stream ID!
                    streamID = p["streamID"]
                    if streamID in streamIDs:
                        continue
                    streamIDs.append(streamID)

                    pick = AdHocPick(p["publicID"])
                    pick.networkCode = p["networkCode"]
                    pick.stationCode = p["stationCode"]
                    pick.locationCode = p["locationCode"]
                    pick.channelCode = p["channelCode"]
                    pick.time = p["time"]
                    try:
                        pick.phaseHint = p["phaseHint"]
                    except KeyError:
                        pick.phaseHint = "P"
                    adhoc_picks.append(pick)

            try:
                logger.info("PROCESS begin")
                new_picks = self._process(adhoc_picks, eventID)
                logger.info("PROCESS end")
            except RuntimeError as e:
                logger.warning(str(e))
                return

            outgoing_yaml = list()
            for new_pick in new_picks:
                p = dict()
                p["publicID"] = new_pick.publicID
                p["networkCode"] = new_pick.networkCode
                p["stationCode"] = new_pick.stationCode
                p["locationCode"] = new_pick.locationCode
                p["channelCode"] = new_pick.channelCode
                p["time"] = new_pick.time
                p["confidence"] = float("%.3f" % new_pick.confidence)
                p["model"] = self.model_name
                outgoing_yaml.append(p)

            if self.test:
                logger.info("+++test mode - stopping")
                return True

            # produce the output

            # directory to which we write the resulting yaml files
            out_d = os.path.join(eventDir, "out")

            outgoing_d = os.path.join(self.workingDir, "outgoing")

            os.makedirs(outgoing_d, exist_ok=True)

            d, f = os.path.split(link)

            if not outgoing_yaml:
                logging.warning("no results - exiting")
                os.remove(link)
                return

            os.makedirs(out_d, exist_ok=True)
            yamlFileName = os.path.join(out_d, f)
            with open(yamlFileName, 'w') as file:
                yaml.dump(outgoing_yaml, file)

            dst = os.path.join(outgoing_d, f)
            # TODO: clean up!
            src = os.path.join("..", self.eventRootDir, eventID, "out", f)

            try:
                logging.debug("creating symlink %s -> %s" % (dst, src))
                os.symlink(src, dst)
            except FileExistsError as e:
                logging.warning("symlink  %s -> %s" % (dst, src))

            # we are done with this item
            os.remove(link)

            # If in reverse mode, break after first processed item
            # in order to check if there are new items, which will
            # then also be processed first. If not in reverse mode
            # we don't care.
            if reverse:
                break

    def run(self):
        """Main loop"""

        while True:
            self._poll()
            time.sleep(1)

            if self.exit:
                logger.info("+++exit mode - exiting")
                break

        return True


    def _ml_predict(self, adhoc_picks, eventID):
        """ Takes a list of AdHocPick instances, repicks them,
        fills a dictionary with those predictions,
        each a (Time, confidence) pair, and returns it.

        Returns:
            dict: a dictionary of `pickID: (time, confidence)` pairs
        """

        def fill_result(predictions, stream, collected_adhoc_picks, annot_d):
            """Fills `predictions` with annotations done by the model
               using the stream. Additional data will be grabbed from
               `collected_adhoc_picks`.
            """
            annotations, assoc_ind = None, None
            try:

                # ************ Model call ****************#
                annotations = self.model.annotate(stream)

                # Only use those predictions that were done for P wave onsets
                annotations = list(filter(
                    lambda a: a.id.split('.')[-1].endswith('_P'),
                    annotations))

                # indexes list of successfully associated annotations
                assoc_ind = []
                for i, annotation in enumerate(annotations):
                    try:
                        # Associate the annotation to an AdHocPick

                        pick = next(filter(
                            lambda p:
                            p.networkCode == annotation.meta.network and
                            p.stationCode == annotation.meta.station and
                            p.locationCode == annotation.meta.location,
                            collected_adhoc_picks))
                    except StopIteration:
                        logger.warning(
                            "failed to associate annotation for %s.%s" % (
                                annotation.meta.network,
                                annotation.meta.station))

                        # No AdHocPick could be found that matches the
                        # current annotation. The reason for this could be
                        # a gap in waveform data such that two traces of
                        # the same stations are passed to the model
                        # therefore the model predicts a second time, but
                        # since no AdHocPick is waiting for it, this
                        # prediction will be discarded. This problem should
                        # be addressed in future versions by providing clean
                        # data, beforehand, because it would be too difficult
                        # to decide right here which pick is the better one
                        # resp. the one wanted.
                        continue

                    assoc_ind.append(i)
                    nslc = (
                        pick.networkCode,
                        pick.stationCode,
                        pick.locationCode,
                        pick.channelCode )
                    annot_f = os.path.join(annot_d, "%s.%s.%s.%s.sac" % nslc)
                    annotation.write(annot_f, format="SAC")

                    confidence = annotation.data.astype(np.double)
                    times = annotation.times()
                    peaks, _ = scipy.signal.find_peaks(confidence, height=0.1)
                    for peak in peaks:
                        picktime = annotation.stats.starttime + times[peak]
                        if pick.publicID not in predictions:
                            predictions[pick.publicID] = []
                        new_item = (picktime, confidence[peak])
                        predictions[pick.publicID].append(new_item)
                        logger.debug("#### " + pick.publicID +
                                     "  %.3f" % confidence[peak])

                    collected_adhoc_picks.remove(pick)

            except (TypeError, ValueError, ZeroDivisionError) as e:
                logger.error("Caught "+repr(e))

            if None not in [annotations, assoc_ind]:

                # Clean annotations from those who were associated successfully
                [annotations.pop(i) for i in sorted(assoc_ind, reverse=True)]

                left_annos_n = len(annotations)
                left_adhocs_n = len(collected_adhoc_picks)
                if left_annos_n > 0:
                    logger.warning(
                        f"There were {left_annos_n} annotations that "
                        "could not be associated.")
                if left_adhocs_n > 0:
                    logger.warning(
                        f"There were {left_adhocs_n} AdHoc picks for "
                        "which no annotation was done.")

        # end of fill_result()


        logger.info("ML predictions starts...")

        annotations_dir = os.path.join(
            self.eventRootDir, eventID, self.annotDir)
        os.makedirs(annotations_dir, exist_ok=True)

        acc_predictions = {}
        picks_remain_size = picks_all_size = len(adhoc_picks)
        start_index, end_index = 0, min(self.batchSize, picks_all_size)

        # **** Batch loop:  *****#
        while picks_remain_size > 0:

            picks_batch = adhoc_picks[start_index:end_index]
            
            try:
                _eventID, stream, collected_adhoc_picks = \
                    self._get_stream_from_picks(picks_batch, eventID)
            except Exception:
                stream = None

            if stream is not None:
                # In some cases no picks are returned, nonetheless this
                # could be true for the current batch of picks only, the
                # next batch could be ok, therefore we just need to pass
                # the following line
                fill_result(
                    acc_predictions, stream, collected_adhoc_picks,
                    annotations_dir)

            # Updating
            picks_remain_size -= self.batchSize
            start_index += self.batchSize
            end_index = min(
                start_index + self.batchSize,
                start_index + picks_remain_size)

        logger.info("...ML prediction ended.")
        return acc_predictions


##########################################################################
# Attention: "TEPRepicker" and "TEPSWRepicker" are not available by standard
# seisbench servers yet, you need to provide the corresponding files manually
# in    ~/.seisbench/models/tepsw (old TEP)
# resp. ~/.seisbench/models/tep   (new TEP).
#
# Note that the file names without suffix must match the string (dataset)
# passed to from_pretrained() below.
##########################################################################


class TEPRepicker(Repicker, ABC):
    """Transformer Earthquake Picker"""

    def __init__(self, dataset='geofon', **kwargs):
        if TEP is None:
            raise NotImplementedError("Model 'tep' not available")
        self.model_name = "tep"
        self.model = TEP.from_pretrained(dataset)
        super(TEPRepicker, self).__init__(**kwargs)
        if self.minConfidence is None:
            self.minConfidence = 0.3


class TEPSWRepicker(Repicker, ABC):
    """Transformer Earthquake Picker with Sliding Window"""

    def __init__(self, dataset='geofon', **kwargs):
        if TEPSW is None:
            raise NotImplementedError("Model 'tepsw' not available")
        self.model_name = "tep"
        self.model = TEPSW.from_pretrained(dataset)
        super(TEPSWRepicker, self).__init__(**kwargs)
        if self.minConfidence is None:
            self.minConfidence = 0.3


##########################################################################

# OTHER MODELS


class EQTransformerRepicker(Repicker, ABC):
    """EQTransformer"""

    def __init__(self, dataset='geofon', **kwargs):
        self.model_name = 'eqtransformer'
        self.model = EQTransformer.from_pretrained(dataset)
        super(EQTransformerRepicker, self).__init__(**kwargs)
        if self.minConfidence is None:
            self.minConfidence = 0.2


class PhaseNetRepicker(Repicker, ABC):
    """PhaseNet"""

    def __init__(self, dataset='geofon', **kwargs):
        self.model_name = 'phasenet'
        self.model = PhaseNet.from_pretrained(dataset)
        super(PhaseNetRepicker, self).__init__(**kwargs)
        if self.minConfidence is None:
            self.minConfidence = 0.3 # UNTESTED


##########################################################################

# Providing strings for the available picker model classes that can be
# used as arguments for the script

MODEL_MAP = {
    'tep': TEPRepicker,
    'tepsw': TEPSWRepicker,
    'phasenet': PhaseNetRepicker,
    'eqtransformer': EQTransformerRepicker,
}


def main(model, bs, t, e, dev, wkdir, evdir, spdir, andir, ds, conf):
    repicker = MODEL_MAP[model](
        dataset=ds,
        test=t,
        exit=e,
        batchSize=bs,
        workingDir=wkdir,
        eventRootDir=evdir,
        spoolDir=spdir,
        annotDir=andir,
        device=dev,
        minConfidence=conf
    )
    repicker.run()


if __name__ == '__main__':
    models = list(MODEL_MAP.keys())
    parser = argparse.ArgumentParser(description='SeicComp Client - ML Repicker using SeisBench')
    parser.add_argument(
        '--model', choices=models, default=models[0], dest='model',
        help=f"Choose one of the available ML models to make the predictions."
             f" Note that if the model is not cached, it might take a " \
             f"little while to download the weights file."
             f" Also note that for tep and tepsw you will need to download and install the necessary "
             f"files manually.")
    parser.add_argument(
        '--test', action='store_true',
        help='Prevents the repicker from writing out outgoing yaml with refined picks.')
    parser.add_argument(
        '--exit', action='store_true',
        help='Exit after items in spool folder have been processed')
    parser.add_argument(
        '--bs', '--batch-size', action='store_const', const=50, default=50, dest='batchSize',
        help="Choose a batch size that is suitable for the machine you are working on. Defaults to 50.")
    parser.add_argument(
        '--device', choices=['cpu', 'gpu'], default='cpu',
        help="If you have access to cuda device change this parameter to 'gpu'.")
    parser.add_argument(
        '--working-dir', type=str, default='.', dest='workingDir',
        help="Working directory where all files are placed and exchanged")
    parser.add_argument(
        '--event-dir', type=str, default='', dest='eventRootDir',
        help="Where to look for event folders with waveforms and picks and where to store annotations "
            "per each event")
    parser.add_argument(
        '--spool-dir', type=str, default='', dest='spoolDir',
        help="Where to look for new symlinks to YAML files that can be processed by the repicker.")
    parser.add_argument(
        '--annot-dir', type=str, default="annot", dest='annotDir',
        help="Where to write the annotations to, inside events/<event>/.")
    parser.add_argument(
        '--outgoing-dir', type=str, default='', dest='outgoingDir',
        help="outgoing directory where all result files are written")
    parser.add_argument(
        '--dataset', type=str, default='geofon', dest='dataset',
        help="The dataset on which the model was predicted. Defaults to geofon.")
    parser.add_argument(
        '--min-confidence', type=float, default=0.3, dest='minConfidence',
        help="Confidence threshold below which a pick is skipped. Defaults to 0.3")
    args = parser.parse_args()

    if not args.eventRootDir:
        args.eventRootDir = os.path.join(args.workingDir, "events")
    if not args.spoolDir:
        args.spoolDir = os.path.join(args.workingDir, "spool")
    if not args.outgoingDir:
        args.outgoingDir = os.path.join(args.workingDir, "outgoing")

    main(
        args.model,
        bs=args.batchSize,
        t=args.test,
        e=args.exit,
        dev=args.device,
        wkdir=args.workingDir,
        evdir=args.eventRootDir,
        spdir=args.spoolDir,
        andir=args.annotDir,
        ds=args.dataset,
        conf=args.minConfidence
    )
