import json

import pytest

from test_pipeline_visual import EXTRACTION
from vision_extract import ExtractionError, _parse_and_validate


@pytest.mark.parametrize("payload", [[], 2, None, {**EXTRACTION, "model": ["CASCADIA"]}, {**EXTRACTION, "make": 42}])
def test_malformed_extraction_is_rejected(payload):
    with pytest.raises(ExtractionError):
        _parse_and_validate(json.dumps(payload))
