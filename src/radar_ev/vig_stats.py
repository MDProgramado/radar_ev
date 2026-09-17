"""vig_stats.py — Contadores de remoção de vig por execução do pipeline.

Registra para cada avaliação de EV se o par Over/Under foi encontrado
(vig removido) ou se caiu em fallback (odd bruta). Também registra o
sub-caso do fallback em que existe complemento apenas com limiar
divergente (ex: over_9.5 sem under_9.5, mas com under_10.5 na lista) —
candidato a interpolação futura. Expõe propriedades para percentuais e um
singleton ``vig_stats`` reutilizável entre chamadas de
``_calculate_ev_without_vig``.
"""

from __future__ import annotations


class VigStats:
    """Contadores in-memory de uso de vig removido vs. fallback."""

    def __init__(self) -> None:
        self.with_vig: int = 0
        self.fallback: int = 0
        self.fallback_limiar_divergente: int = 0

    def record(self, vig_removed: bool) -> None:
        """Registra uma avaliação de EV."""
        if vig_removed:
            self.with_vig += 1
        else:
            self.fallback += 1

    def record_divergent(self) -> None:
        """Registra um fallback com complemento de limiar divergente.

        Subconjunto de ``fallback``: só faz sentido quando par da mesma
        linha não foi encontrado.
        """
        self.fallback_limiar_divergente += 1

    def reset(self) -> None:
        """Zera contadores (chamar no início de cada execução do pipeline)."""
        self.with_vig = 0
        self.fallback = 0
        self.fallback_limiar_divergente = 0

    @property
    def total(self) -> int:
        return self.with_vig + self.fallback

    @property
    def vig_percent(self) -> float:
        return (self.with_vig * 100.0 / self.total) if self.total else 0.0

    @property
    def fallback_percent(self) -> float:
        return (self.fallback * 100.0 / self.total) if self.total else 0.0

    @property
    def divergente_percent(self) -> float:
        return (self.fallback_limiar_divergente * 100.0 / self.total) if self.total else 0.0

    def __repr__(self) -> str:
        return (
            f"VigStats(with_vig={self.with_vig}, fallback={self.fallback}, "
            f"fallback_limiar_divergente={self.fallback_limiar_divergente})"
        )


vig_stats = VigStats()
