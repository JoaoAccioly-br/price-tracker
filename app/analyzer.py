"""Módulo de análise de promoções e detecção de ofertas falsas (metade do dobro).

Analisa o histórico de preços de um produto em janelas de 30 e 60 dias para
determinar se um desconto é genuíno ou se o preço foi inflado antes de baixar.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import statistics

from .db import get_conn


@dataclass
class PriceAnalysis:
    verdict: str  # "all_time_low", "real_deal", "fake_deal", "normal", "insufficient_data"
    label: str
    badge_class: str
    description: str
    current_price: float | None
    avg_30d: float | None
    min_30d: float | None
    max_30d: float | None
    avg_60d: float | None
    min_60d: float | None
    diff_vs_avg_30d_pct: float | None  # % relativo à média 30d (negativo = desconto)
    diff_vs_avg_60d_pct: float | None
    days_tracked: int
    is_fake_deal: bool = False
    is_real_deal: bool = False
    is_all_time_low: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_ts(ts) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    clean = str(ts).replace("T", " ")[:19]
    try:
        return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def analyze_price_history(
    records: list[dict],
    current_price: float | None = None,
    now: datetime | None = None,
) -> PriceAnalysis:
    """Calcula métricas históricas de 30 e 60 dias e classifica a qualidade da oferta."""
    now_dt = now or datetime.now(timezone.utc)

    # Filtra e padroniza registros com preços válidos
    valid_records = []
    for r in records:
        eff_price = r.get("final_price") if r.get("final_price") is not None else r.get("price")
        if eff_price is not None and eff_price > 0:
            dt = _parse_ts(r.get("checked_at"))
            valid_records.append({"price": float(eff_price), "dt": dt})

    valid_records.sort(key=lambda x: x["dt"])

    if not valid_records:
        return PriceAnalysis(
            verdict="insufficient_data",
            label="⏳ Sem Histórico",
            badge_class="badge-collecting",
            description="Ainda não há registros de preços gravados para análise.",
            current_price=current_price,
            avg_30d=None,
            min_30d=None,
            max_30d=None,
            avg_60d=None,
            min_60d=None,
            diff_vs_avg_30d_pct=None,
            diff_vs_avg_60d_pct=None,
            days_tracked=0,
        )

    curr_p = float(current_price) if current_price is not None else valid_records[-1]["price"]

    first_dt = valid_records[0]["dt"]
    days_tracked = max(1, (now_dt - first_dt).days + 1)

    # Janelas temporais
    t_30d = now_dt - timedelta(days=30)
    t_60d = now_dt - timedelta(days=60)
    t_7d = now_dt - timedelta(days=7)

    records_60d = [r for r in valid_records if r["dt"] >= t_60d]
    records_30d = [r for r in valid_records if r["dt"] >= t_30d]
    records_recent_7d = [r for r in valid_records if r["dt"] >= t_7d]
    records_prior = [r for r in records_30d if r["dt"] < t_7d]

    # Estatísticas 30 dias
    prices_30d = [r["price"] for r in records_30d]
    avg_30d = round(statistics.mean(prices_30d), 2) if prices_30d else None
    min_30d = round(min(prices_30d), 2) if prices_30d else None
    max_30d = round(max(prices_30d), 2) if prices_30d else None

    # Estatísticas 60 dias
    prices_60d = [r["price"] for r in records_60d]
    avg_60d = round(statistics.mean(prices_60d), 2) if prices_60d else None
    min_60d = round(min(prices_60d), 2) if prices_60d else None

    # Variações percentuais relativas
    diff_30d_pct = round(((curr_p - avg_30d) / avg_30d) * 100, 1) if avg_30d else None
    diff_60d_pct = round(((curr_p - avg_60d) / avg_60d) * 100, 1) if avg_60d else None

    # 1. Histórico insuficiente (mínimo de 7 dias de monitoramento para veredito estatístico)
    if days_tracked < 7 or len(valid_records) < 3:
        return PriceAnalysis(
            verdict="insufficient_data",
            label="⏳ Coletando Dados",
            badge_class="badge-collecting",
            description=f"Monitorado há {days_tracked} dia(s). São necessários ao menos 7 dias de histórico para detectar padrões de promoção.",
            current_price=curr_p,
            avg_30d=avg_30d,
            min_30d=min_30d,
            max_30d=max_30d,
            avg_60d=avg_60d,
            min_60d=min_60d,
            diff_vs_avg_30d_pct=diff_30d_pct,
            diff_vs_avg_60d_pct=diff_60d_pct,
            days_tracked=days_tracked,
        )

    # 2. Detecção de Promoção Falsa / Inflação Pré-Desconto ("Metade do Dobro"):
    # Cenário A: O preço subiu recentemente nos últimos 7 dias (>10% acima da média anterior)
    # e agora caiu, mas ainda está igual ou maior que a média anterior.
    inflated_recently = False
    if records_recent_7d and records_prior:
        recent_max = max(r["price"] for r in records_recent_7d)
        prior_avg = statistics.mean(r["price"] for r in records_prior)
        if prior_avg > 0 and (recent_max - prior_avg) / prior_avg >= 0.10:
            if curr_p >= (prior_avg * 0.98):
                inflated_recently = True

    # Cenário B: Preço atual está mais de 4% acima da média de 30 dias
    price_above_average = (diff_30d_pct is not None and diff_30d_pct >= 4.0)

    if inflated_recently or price_above_average:
        reason = (
            f"Preço sofreu inflação artificial nos últimos dias e está acima do valor praticado anteriormente (média 30d: R$ {avg_30d:.2f})."
            if inflated_recently
            else f"O preço atual está {diff_30d_pct:.1f}% ACIMA da média dos últimos 30 dias (R$ {avg_30d:.2f})."
        )
        return PriceAnalysis(
            verdict="fake_deal",
            label="🔴 Alerta: Promoção Falsa",
            badge_class="badge-fake-deal",
            description=reason,
            current_price=curr_p,
            avg_30d=avg_30d,
            min_30d=min_30d,
            max_30d=max_30d,
            avg_60d=avg_60d,
            min_60d=min_60d,
            diff_vs_avg_30d_pct=diff_30d_pct,
            diff_vs_avg_60d_pct=diff_60d_pct,
            days_tracked=days_tracked,
            is_fake_deal=True,
        )

    # 3. Menor Preço Histórico em 60 Dias
    if min_60d is not None and curr_p <= min_60d and (diff_30d_pct is not None and diff_30d_pct <= -3.0):
        pct_txt = f"{abs(diff_30d_pct):.0f}% OFF" if diff_30d_pct else ""
        return PriceAnalysis(
            verdict="all_time_low",
            label=f"🔥 Menor Preço em 60d ({pct_txt})".strip(),
            badge_class="badge-all-time-low",
            description=f"Menor preço registrado em 60 dias! Está {abs(diff_30d_pct):.1f}% mais barato que a média recente (R$ {avg_30d:.2f}).",
            current_price=curr_p,
            avg_30d=avg_30d,
            min_30d=min_30d,
            max_30d=max_30d,
            avg_60d=avg_60d,
            min_60d=min_60d,
            diff_vs_avg_30d_pct=diff_30d_pct,
            diff_vs_avg_60d_pct=diff_60d_pct,
            days_tracked=days_tracked,
            is_all_time_low=True,
            is_real_deal=True,
        )

    # 4. Promoção Real (Ao menos 5% abaixo da média dos 30 dias)
    if diff_30d_pct is not None and diff_30d_pct <= -5.0:
        return PriceAnalysis(
            verdict="real_deal",
            label=f"🟢 Promoção Real (-{abs(diff_30d_pct):.0f}%)",
            badge_class="badge-real-deal",
            description=f"Desconto genuíno! O preço atual está {abs(diff_30d_pct):.1f}% abaixo da média de 30 dias (R$ {avg_30d:.2f}).",
            current_price=curr_p,
            avg_30d=avg_30d,
            min_30d=min_30d,
            max_30d=max_30d,
            avg_60d=avg_60d,
            min_60d=min_60d,
            diff_vs_avg_30d_pct=diff_30d_pct,
            diff_vs_avg_60d_pct=diff_60d_pct,
            days_tracked=days_tracked,
            is_real_deal=True,
        )

    # 5. Preço Normal (Dentro da faixa usual de mercado)
    diff_str = f"({diff_30d_pct:+.1f}%)" if diff_30d_pct is not None else ""
    return PriceAnalysis(
        verdict="normal",
        label=f"⚪ Preço Normal {diff_str}".strip(),
        badge_class="badge-normal",
        description=f"Preço dentro do padrão de mercado (média 30d: R$ {avg_30d:.2f} | 60d: R$ {avg_60d or avg_30d:.2f}).",
        current_price=curr_p,
        avg_30d=avg_30d,
        min_30d=min_30d,
        max_30d=max_30d,
        avg_60d=avg_60d,
        min_60d=min_60d,
        diff_vs_avg_30d_pct=diff_30d_pct,
        diff_vs_avg_60d_pct=diff_60d_pct,
        days_tracked=days_tracked,
    )


def get_product_analysis(product_id: int, current_price: float | None = None, conn=None) -> PriceAnalysis:
    """Busca o histórico recente (65 dias) no banco de dados e gera a análise de promoção."""
    close_at_end = False
    if conn is None:
        conn = get_conn()
        close_at_end = True

    try:
        cutoff_65d = (datetime.now(timezone.utc) - timedelta(days=65)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            """SELECT pr.price, pr.final_price, pr.checked_at
               FROM prices pr
               JOIN listings l ON l.id = pr.listing_id
               WHERE l.product_id = ?
                 AND (pr.available = 1 OR pr.available IS NULL)
                 AND pr.checked_at >= ?
               ORDER BY pr.checked_at ASC""",
            (product_id, cutoff_65d),
        ).fetchall()
        records = [dict(r) for r in rows]
        return analyze_price_history(records, current_price=current_price)
    finally:
        if close_at_end:
            conn.close()

