# 2023-08-30

## Added basic support for manual picks

If a manual origin comes with manual picks with weight greater zero, we keep the manual picks and don't replace them by DL picks. Automatic picks in a manual origin are still replaced by DL picks. The advantage of this is especially the adoption of depth phase and S picks. But also manual P picks are given preference over DL P picks.
