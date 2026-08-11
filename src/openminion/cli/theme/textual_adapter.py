from .models import Theme


def theme_variables_dict(theme: Theme) -> dict[str, str]:
    return {
        f"openminion-{name.replace('_', '-')}": getattr(theme, name)
        for name in theme.color_field_names()
    }


def as_tcss_preamble(theme: Theme) -> str:
    lines = [
        f"/* OpenMinion shared theme: {theme.name} */",
        *(
            f"${name}: {value};"
            for name, value in theme_variables_dict(theme).items()
        ),
    ]
    return "\n".join(lines) + "\n"


__all__ = ["as_tcss_preamble", "theme_variables_dict"]
