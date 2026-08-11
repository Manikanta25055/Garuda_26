"""Decide whether an utterance is already covered by a learned rule.

Every local hit is a cloud call that did not happen, which is exactly what the
evaluation measures. Two backends are provided so the comparison between them
is a result rather than an implementation detail.
"""
import logging

log = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz
    FUZZ_AVAILABLE = True
except Exception:
    fuzz = None
    FUZZ_AVAILABLE = False

_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class LocalMatcher:
    def __init__(self, store, backend="fuzzy", threshold=0.72):
        if backend not in ("fuzzy", "embed"):
            raise ValueError(f"unknown backend: {backend}")
        self.store = store
        self.backend_name = backend
        self.threshold = threshold
        self._encoder = None
        if backend == "embed":
            self._encoder = self._load_encoder()
            if self._encoder is None:
                log.warning("embedding backend unavailable -- falling back to fuzzy")
                self.backend_name = "fuzzy"

    @staticmethod
    def _load_encoder():
        try:
            from sentence_transformers import SentenceTransformer
            return SentenceTransformer(_EMBED_MODEL)
        except Exception as exc:
            log.warning("could not load %s: %s", _EMBED_MODEL, exc)
            return None

    def _score(self, utterance, candidate):
        if self.backend_name == "embed" and self._encoder is not None:
            from sentence_transformers import util
            vectors = self._encoder.encode([utterance, candidate], convert_to_tensor=True)
            return float(util.cos_sim(vectors[0], vectors[1]).item())
        if not FUZZ_AVAILABLE:
            return 1.0 if utterance.strip().lower() == candidate.strip().lower() else 0.0
        return fuzz.token_set_ratio(utterance.lower(), candidate.lower()) / 100.0

    def match(self, utterance):
        """Return the closest enabled rule above threshold, or None."""
        best, best_score = None, 0.0
        for rule in self.store.rules:
            if not rule.get("enabled", True):
                continue
            score = self._score(utterance, rule.get("source_utterance", ""))
            if score > best_score:
                best, best_score = rule, score
        return best if best_score >= self.threshold else None
