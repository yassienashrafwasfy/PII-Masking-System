"""Platform-owned OCR for scanned and image-bearing PDF pages.

This package lives under ``src/models/`` rather than beside the document
pipeline in ``app/services/`` for one reason: ``masking_engine/__init__.py``
imports every ``app.services.*`` module it exports **eagerly**, so a module
that touches ONNX Runtime placed there would pull the runtime into every
process that imports the package -- the gateway image included. Everything
under ``src/models/`` is reachable only through that module's ``__getattr__``.

The name is a mild misfit: ``src/models/`` otherwise means "one team's
detector", and this is platform-owned and shared. It sits here for the
lazy-import guarantee, not because it belongs to a team.

``preprocess``, ``db_postprocess`` and ``ctc_decode`` are pure numpy and carry
no model artifact, so they are importable and testable on their own. Only
``ocr_engine`` constructs an inference session.
"""
