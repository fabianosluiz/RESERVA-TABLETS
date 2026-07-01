"""
Importa os dados históricos do arquivo dados_originais.xlsx (aba "Reservas")
para o banco de dados do sistema, e ajusta o total de tablets do estoque
com base na aba "Estoque".

Uso:
    python import_dados.py
"""
import math
from datetime import datetime, date, timedelta

import pandas as pd

from app import app, db, Reserva, Estoque, User

EXCEL_PATH = "dados_originais.xlsx"
EXCEL_EPOCH = date(1899, 12, 30)  # data base do Excel


def excel_serial_to_date(value):
    try:
        serial = float(value)
        if serial < 20000 or serial > 80000:
            return None
        return EXCEL_EPOCH + timedelta(days=int(serial))
    except (ValueError, TypeError):
        return None


def excel_fraction_to_time(value):
    try:
        frac = float(value)
        if frac < 0 or frac >= 1:
            return None
        total_minutes = round(frac * 24 * 60)
        h, m = divmod(total_minutes, 60)
        return f"{h:02d}:{m:02d}"
    except (ValueError, TypeError):
        return None


def to_int_safe(value):
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return int(float(value))
    except (ValueError, TypeError):
        return None


def parse_data(value):
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.date()
    if isinstance(value, date):
        return value
    return excel_serial_to_date(value)


def parse_hora(value):
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%H:%M")
    if hasattr(value, "hour"):  # datetime.time
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, (int, float)):
        return excel_fraction_to_time(value)
    return None


def importar_reservas():
    df = pd.read_excel(EXCEL_PATH, sheet_name="Reservas")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")

    importados = 0
    ignorados = 0

    for _, row in df.iterrows():
        nome = str(row.get("Nome") or "").strip()
        if not nome or nome.lower() == "nan":
            ignorados += 1
            continue

        data_reserva = parse_data(row.get("Data"))
        if not data_reserva:
            ignorados += 1
            continue

        quantidade = to_int_safe(row.get("Quantidade solicitada"))
        if not quantidade or quantidade <= 0:
            ignorados += 1
            continue

        qtd_devolvida = to_int_safe(row.get("Quantidade devolvida"))
        retirada_raw = row.get("Retirada")
        foi_retirado = isinstance(retirada_raw, str) and retirada_raw.strip() != ""

        if qtd_devolvida and qtd_devolvida > 0:
            status = "devolvido"
        elif foi_retirado:
            status = "retirado"
        else:
            status = "devolvido"  # dados históricos: assume concluído

        turma = str(row.get("Turma") or "").strip()
        atividade = str(row.get("Atividade") or "").strip()

        reserva = Reserva(
            nome=nome,
            turma=turma if turma and turma.lower() != "nan" else None,
            data=data_reserva,
            horario_retirada=parse_hora(row.get("Horário de retirada")),
            horario_devolucao=parse_hora(row.get("Horário de devolução")),
            atividade=atividade if atividade and atividade.lower() != "nan" else None,
            quantidade_solicitada=quantidade,
            quantidade_devolvida=qtd_devolvida or 0,
            status=status,
            professor_id=None,
        )
        db.session.add(reserva)
        importados += 1

    db.session.commit()
    print(f"Reservas importadas: {importados} | ignoradas (linhas inválidas): {ignorados}")


def ajustar_estoque():
    df = pd.read_excel(EXCEL_PATH, sheet_name="Estoque")
    if "disponíveis" in df.columns:
        # usa o valor mais frequente de "disponíveis", que reflete o total
        # cadastrado nos dias sem reservas em aberto (evita outliers)
        moda = df["disponíveis"].mode()
        total_estimado = to_int_safe(moda.iloc[0]) if not moda.empty else 70
        total_estimado = total_estimado or 70
    else:
        total_estimado = 70

    estoque = Estoque.get()
    estoque.total_tablets = total_estimado
    db.session.commit()
    print(f"Total de tablets no estoque definido como: {total_estimado}")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if Reserva.query.count() > 0:
            confirmacao = input("Já existem reservas no banco. Importar mesmo assim pode duplicar dados. Continuar? (s/N) ")
            if confirmacao.strip().lower() != "s":
                print("Importação cancelada.")
                raise SystemExit(0)
        importar_reservas()
        ajustar_estoque()
        print("Importação concluída.")
