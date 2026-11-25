"""Entry point and shared helpers kept at build_pacman.py for the workflow and build_links.py."""

from build_snake import CANVAS_W, bitmap, main, mix, sprite_path, validate_smil

CLAUDE_CORAL = {"dark": "#D97757", "light": "#C15F3C"}


def loop(attr: str, values: list[str], dur: float) -> str:
    """A short free-running cycle, independent of the master clock."""
    n = len(values)
    keys = ";".join(f"{i / n:.4f}" for i in range(n))
    return (
        f'<animate attributeName="{attr}" values="{";".join(values)}" '
        f'keyTimes="{keys}" dur="{dur}s" calcMode="discrete" repeatCount="indefinite"/>'
    )


if __name__ == "__main__":
    main()
