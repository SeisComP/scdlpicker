# 0.1.1

## Added basic support for manual picks

If a manual origin comes with manual picks with weight greater zero, we keep the manual picks and don't replace them by DL picks. Automatic picks in a manual origin are still replaced by DL picks. The advantage of this is especially the adoption of depth phase and S picks. But also manual P picks are given preference over DL P picks.

# 0.2.0

## Code simplification and cleanups

- use of `pathlib` instead of `os.path`
- removal of options to specify subdirectory names (really not needed)
- only the working directory is specified, all subdirectories have fixed naming
- changed config prefix from `mlpicker` to `scdlpicker`
- renaming of some internal variables to make them more consistent between modules
