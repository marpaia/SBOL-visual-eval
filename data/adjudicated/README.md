# Adjudicated figure labels

This directory is a separate expert-reviewed label layer. Historical paper counts under `data/processed/` remain immutable.

Each adjudication run writes a manifest and `decisions.csv`. The compatibility fields copied from history are exact figure labels entailed by saturated paper counts; blank `adjudicated_*`, rationale, reviewer, and timestamp fields are filled only by an expert review.

The generated `index.html` and `images/` gallery remain local because source-paper licenses are heterogeneous. Regenerate them from a committed compatibility report and the local corpus with `sbol-visual-eval adjudicate`.
