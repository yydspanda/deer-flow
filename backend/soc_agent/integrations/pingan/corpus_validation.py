"""PingAn-only scope exclusions for the two-batch Memory validation corpus."""


def is_corpus_validation_excluded(*, rule_code: str | None, source_type: str | None, topic: str | None) -> bool:
    # Upstream already judges this detector. Keep the source records, but do not
    # repeat its analysis or learn from it in the corpus validation experiment.
    return rule_code == "RPAADM_002192" and (source_type or "").casefold() == "siem" and topic == "T_GBD_zeus_data"
