"""Identity stamps carried on every OPUS artefact."""

from __future__ import annotations

ENGINE_NAME = "OPUS"

# Bumped when the emitted signal contract changes.
ENGINE_VERSION = "opus.v1.0.0"

# Bumped when indicator maths or conviction weighting changes, which
# invalidates any stored probability calibration fitted under the old model.
SCORE_MODEL_VERSION = "opus.score.v1"

# Bumped when the triple-barrier labelling definition changes, which
# invalidates stored outcome labels.
LABEL_MODEL_VERSION = "opus.label.v1"

__all__ = [
    "ENGINE_NAME",
    "ENGINE_VERSION",
    "SCORE_MODEL_VERSION",
    "LABEL_MODEL_VERSION",
]
