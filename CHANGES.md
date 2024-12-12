# 0.1.1

## Added basic support for manual picks

If a manual origin comes with manual picks with weight greater zero, we keep the manual picks and don't replace them by DL picks. Automatic picks in a manual origin are still replaced by DL picks. The advantage of this is especially the adoption of depth phase and S picks. But also manual P picks are given preference over DL P picks.

# 0.2.0

Code simplification and cleanups

- use of `pathlib` instead of `os.path`
- removal of options to specify subdirectory names (really not needed)
- only the working directory is specified, all subdirectories have fixed naming
- changed config prefix from `mlpicker` to `scdlpicker`
- renaming of some internal variables to make them more consistent between modules

# 0.3.0

- added depth phase support

# 0.3.1

- improved configuration 

# 0.3.4

- dedicated config module to avoid config code duplication
- use SeisComP Application class also in repicker module for consistency
- removed scstuff dependency

# 0.4.0

Better integration into SeisComP build

- renamed `scdlpicker.xyz.py` to `scdlpicker-xyz.py`
- will be installed as `scdlpicker-xyz` now
- added default config files
- added description files
- added cmake support
- introduced hack to allow P picking using only the vertical component
