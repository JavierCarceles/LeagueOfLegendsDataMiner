"""Nombres de las guías («Campeón_vs_Rival[_IA].txt»): mismo resultado que la expresión regular de antes."""

import pytest

from app.coaching.guides import parse_guide_name

# Lo que daba ``^(?P<main>.+?)_vs_(?P<opp>.+?)(?P<ia>_IA)?\.txt$`` (sin distinguir mayúsculas) con cada nombre.
CASES = {
    "Darius_vs_Garen.txt": ("Darius", "Garen", False),
    "Darius_vs_Garen_IA.txt": ("Darius", "Garen", True),
    "darius_VS_garen_ia.TXT": ("darius", "garen", True),
    "Dr._Mundo_vs_Lee_Sin.txt": ("Dr._Mundo", "Lee_Sin", False),
    "A_vs_B_vs_C.txt": ("A", "B_vs_C", False),
    "_vs_B.txt": None,
    "A_vs_.txt": None,
    "A_vs_B": None,
    "A_vs__IA.txt": ("A", "_IA", False),
    "A_vs_B_IA_IA.txt": ("A", "B_IA", True),
    "Kai'Sa_vs_Xayah_ia.txt": ("Kai'Sa", "Xayah", True),
    "x.txt": None,
}


@pytest.mark.parametrize(("name", "expected"), CASES.items())
def test_mismo_resultado_que_la_expresion_regular_de_antes(name, expected):
    assert parse_guide_name(name) == expected


def test_un_nombre_enorme_se_lee_al_momento():
    """La expresión de antes podía tardar mucho con nombres largos sin «.txt» al final (retroceso)."""
    assert parse_guide_name("a" + "_vs_" * 5000 + "b") is None


def test_la_carpeta_de_guias_se_indexa_por_rol_campeon_y_rival(tmp_path, dd):
    from app.coaching.guides import GuideIndex

    (tmp_path / "Top").mkdir()
    (tmp_path / "Top" / "Darius_vs_Garen.txt").write_text("vídeo", encoding="utf-8")
    (tmp_path / "Top" / "Darius_vs_Garen_IA.txt").write_text("ia", encoding="utf-8")
    (tmp_path / "Mid").mkdir()
    (tmp_path / "Mid" / "Ahri_vs_Zed_IA.txt").write_text("ia", encoding="utf-8")
    (tmp_path / "Mid" / "notas.txt").write_text("no es una guía", encoding="utf-8")
    (tmp_path / "Mid" / "Nadie_vs_Zed.txt").write_text("campeón desconocido", encoding="utf-8")
    index = GuideIndex(tmp_path, dd)
    top = index.find("TOP", 122, 86)  # Darius vs Garen: la del vídeo gana a la de IA
    assert top["source"] == "Vídeo" and top["content"] == "vídeo" and top["path"].replace("\\\\", "/") == "Top/Darius_vs_Garen.txt"
    assert index.find("MID", 103, 238)["source"] == "IA"
    assert index.exists("JUNGLE", 103, 238)  # sin guía de ese rol, vale la de cualquier rol
    assert not index.exists("MID", 238, 103)
