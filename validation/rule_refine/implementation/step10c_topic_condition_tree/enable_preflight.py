"""Exercise unchanged activation behavior against the repaired installed release."""

import build_command as builder

legacy = builder.load(
    "topic_tree_enable_preflight", builder.ORIGINAL / "enable_preflight.py"
)
legacy.builder = builder


if __name__ == "__main__":
    legacy.run()
