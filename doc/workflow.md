# Workflow inside the scdlpicker

## Programs and files

For pragmatic reasons the workflow was split in two parts.

* `scdlpicker.client.py` is the SeisComP client that connects to both
  the SeisComP messaging and the data server. Its task is to listen
  for new origins as well as waveform data acquisition. The waveform
  data are then picked up by a second program (work horse) to perform
  the actual repicking, the results of which are passed back to the
  `scdlpicker.client.py` and sent to the messaging.

* `scdlpicker.repicker.py` is the work horse that reads parametric and
  waveform data, performs the repicking, and writes the result back
  to disk to be picked up by the SeisComP client `scdlpicker.client.py`.

Information exchange between these two programs is done in a rather
primitive yet robust way.

* All data needed to perform the repicking, both waveforms and
  parametric data, are stored in files within a user-confugurable
  working directory. This directory contains all data on a per-event
  basis.

* Within the working directory there is an event root directory.

* Within the event root directory are event directories, one
  directory per event, the directory name being the event ID.

* The SeisComP client `scdlpicker.client.py` permanently listens to the
  messaging for earthquake parameters. In case of a relevant earthquake
  it additionally acquires the waveforms data needed for repicking.
  It starts by repicking already picked streams but also tries to
  obtain additional picks from unpicked streams.

* Waveform data are written to a `waveforms` subdirectory within the
  respective event directory. Within the `waveforms` directory
  waveform data are saved in MiniSEED format as flat files, one file
  per stream. The origin client takes care of not requesting the
  same waveform data repeatedly.

* Parametric data are directly written to the event directory in
  YAML format. Could also be JSON and format may change in future.
  The current strategy is to *not* listen for and dump all automatic
  picks. Instead, we listen for automatic origins and only consider
  the associated automatic picks, thereby focusing on only the
  relevant picks. For large events with many picks we also look for
  additional picks on unpicked streams. As we only consider origins,
  we write the event parameters as one batch per origin, consisting
  of only picks incl. stream ID, automatic time and public ID. One
  item per pick. That's currently all the repicker needs in addition
  to the waveforms.

* In order to help the repicker find new input data and to initiate
  timely repicking, a symbolic link is written to a `spool` directory
  within the working directory. The link points to the just-added
  parameter data (YAML) file. The repicker thus only needs to
  frequently scan that `spool` directory for symbolic links and after
  finishing work on an item found there, remove the respective link.
  This is a simple and efficient way of IPC as also used e.g. by some
  traditional by Unix emailers.

* As described, the repicker scans the `spool` directory and detects
  presence of a new symbolic link within a second. It then reads the
  respective parameter and waveform data, performs its task and then
  writes the new parameter data (in the same YAML format) to a
  file in the `outgoing` subdirectory of the event directory, where it
  is found and processed by the origin client, which removes the file
  from the `outgoing` subdirectory once its task of sending the
  repicker results is completed.

* All data used in the repicking, incl. waveforms and parametric data
  (input and output) are stored and kept in event directories. The
  only files ever removed are the symbolic links in `spool` and
  `outgoing`. The working directory therefore will accumulate data
  files and old, unneeded files should be removed from time to time.
  It is the responsibility of the user to handle this.


## Example

If `scdlpicker.client.py` is invoked with command-line arguments
`--working-dir $HOME/scdlpicker` then all data will be written to
and read from that directory. An example of how the contents of that
directory are structured is given below.

```
$ ls scdlpicker       
events	outgoing  sent	spool
$ ls scdlpicker/events
gfz2022dxfc gfz2022dxfg
$ ls scdlpicker/events/gfz2022dxfc
annot  in  out	waveforms
$ ls scdlpicker/events/gfz2022dxfc/in 
2022-02-25T09:22:45.698Z.yaml  2022-02-25T09:26:40.897Z.yaml  2022-02-25T09:31:59.159Z.yaml
2022-02-25T09:24:22.454Z.yaml  2022-02-25T09:26:52.454Z.yaml  2022-02-25T09:39:11.641Z.yaml
2022-02-25T09:25:22.454Z.yaml  2022-02-25T09:29:11.873Z.yaml
$ ls scdlpicker/events/gfz2022dxfc/out
2022-02-25T09:22:45.698Z.yaml  2022-02-25T09:26:40.897Z.yaml  2022-02-25T09:31:59.159Z.yaml
2022-02-25T09:24:22.454Z.yaml  2022-02-25T09:26:52.454Z.yaml
2022-02-25T09:25:22.454Z.yaml  2022-02-25T09:29:11.873Z.yaml
$ ls scdlpicker/sent
2022-02-25T09:22:45.698Z.yaml 2022-02-25T09:24:22.454Z.yaml 2022-02-25T09:25:22.454Z.yaml
2022-02-25T09:26:40.897Z.yaml 2022-02-25T09:26:52.454Z.yaml 2022-02-25T09:29:11.873Z.yaml
2022-02-25T09:31:59.159Z.yaml 2022-02-25T09:28:32.454Z.yaml 2022-02-25T09:29:32.454Z.yaml
2022-02-25T09:30:36.688Z.yaml 2022-02-25T09:31:28.074Z.yaml 2022-02-25T09:42:54.251Z.yaml
2022-02-25T09:46:00.179Z.yaml
$ ls scdlpicker/events/gfz2022dxfc/waveforms | grep '^I' | head
II.BORG.00.BH1.mseed
II.BORG.00.BH2.mseed
II.BORG.00.BHZ.mseed
II.CMLA.00.BH1.mseed
II.CMLA.00.BH2.mseed
II.CMLA.00.BHZ.mseed
II.EFI.00.BH1.mseed
II.EFI.00.BH2.mseed
II.EFI.00.BHZ.mseed
II.ERM.00.BH1.mseed
$ ls scdlpicker/events/gfz2022dxfc/annot | grep '^I' | head
II.BORG.00.BH.sac
II.CMLA.00.BH.sac
II.EFI.00.BH.sac
II.ERM.00.BH.sac
II.ESK.00.BH.sac
II.FFC.00.BH.sac
II.JTS.00.BH.sac
II.KDAK.00.BH.sac
II.KURK.00.BH.sac
II.LVZ.00.BH.sac
```
