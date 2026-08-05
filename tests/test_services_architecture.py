"""
`services/` katmanının mimari kurallarını doğrular:
  - Streamlit'e bağımlı değil (import yok, session_state yok)
  - Önbellekleme DI (dependency injection) ile enjekte edilir, servis
    modülleri kendi başlarına Streamlit cache decorator'ı içermez
  - `autocomplete_service`, `search_service`e MODÜL REFERANSIYLA bağımlı
    (`from ... import X` değil) — aksi halde test/monkeypatch'lerin
    `search_service` üzerinde yaptığı değişiklikler görünmez olur

Gerçek Elastic Cloud'a HİÇBİR istek atılmaz.
"""

import ast
import inspect

from services import autocomplete_service, search_service
from services.search_models import PaginationLimitError, SearchResult, SuggestionItem


def _imported_module_names(module) -> set[str]:
    """Bir modülün GERÇEK `import`/`from ... import` ifadelerinden aktarılan
    üst seviye modül adlarını döner (docstring/yorumlardaki metin kaçınılmaz
    olarak "import streamlit" gibi ifadeler İÇEREBİLİR — bu yüzden ham metin
    substring araması yerine `ast` ile gerçek import düğümleri okunur)."""
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _real_code_lines(module) -> str:
    """Modülün kaynağından TÜM docstring/yorum satırlarını çıkarır; yalnızca
    gerçek kod satırları kalır. `session_state` gibi kelimelerin açıklayıcı
    yorumlarda geçmesi (bu dosyadakiler gibi) yanlış-pozitif üretmesin diye."""
    lines = []
    for line in inspect.getsource(module).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    code_only = "\n".join(lines)
    # Üçlü tırnaklı docstring blokları da (satır başına # olmasa da) hariç
    # tutulmalı; ast ile gövdeyi ayrıştırıp yalnızca docstring OLMAYAN
    # düğümlerin kaynak aralığını almak yerine, basitçe modülün/fonksiyonların
    # __doc__'larını metinden çıkarmak yeterli ve daha az kırılgan:
    tree = ast.parse(code_only)
    docstrings = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.append(doc)
    for doc in docstrings:
        code_only = code_only.replace(doc, "")
    return code_only


def test_search_service_has_no_streamlit_import():
    assert "streamlit" not in _imported_module_names(search_service)
    assert "session_state" not in _real_code_lines(search_service)


def test_autocomplete_service_has_no_streamlit_import():
    assert "streamlit" not in _imported_module_names(autocomplete_service)
    assert "session_state" not in _real_code_lines(autocomplete_service)


def test_search_models_has_no_streamlit_import():
    import services.search_models as search_models

    assert "streamlit" not in _imported_module_names(search_models)
    assert "session_state" not in _real_code_lines(search_models)


def test_autocomplete_service_references_search_service_via_module_not_from_import():
    # `from services.search_service import X` KULLANILMAMALI — bu isim
    # kopyalar, testlerin `search_service.X`i monkeypatch etmesini bu
    # modülden görünmez kılar (bkz. services/autocomplete_service.py docstring'i).
    tree = ast.parse(inspect.getsource(autocomplete_service))
    from_search_service_names = set()
    module_level_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "services.search_service":
            from_search_service_names.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module == "services" and node.level == 0:
            module_level_imports.update(alias.name for alias in node.names)

    assert not from_search_service_names, (
        f"autocomplete_service, search_service'ten isim-bazlı import ediyor: {from_search_service_names}"
    )
    assert "search_service" in module_level_imports


def test_fetch_category_aggregations_is_uncached_by_default():
    # Servis katmanında @st.cache_data OLMAMALI — önbellekleme app.py'de yapılır.
    assert not hasattr(search_service.fetch_category_aggregations, "clear")


def test_fetch_suggestion_hits_is_uncached_by_default():
    assert not hasattr(autocomplete_service.fetch_suggestion_hits, "clear")


def test_discover_category_intent_accepts_fetch_aggregations_di():
    sig = inspect.signature(search_service.discover_category_intent)
    assert "fetch_aggregations" in sig.parameters


def test_search_products_accepts_fetch_aggregations_di():
    sig = inspect.signature(search_service.search_products)
    assert "fetch_aggregations" in sig.parameters


def test_get_suggestions_accepts_fetch_hits_di():
    sig = inspect.signature(autocomplete_service.get_suggestions)
    assert "fetch_hits" in sig.parameters


def test_get_suggestions_returns_suggestion_item_dataclasses(monkeypatch):
    def fake_fetch_hits(query_text, result_size):
        return (
            [{
                "_source": {
                    "parent_asin": "B0X",
                    "title": "Wireless Headphones",
                    "store": "Acme",
                    "price": 29.99,
                    "average_rating": 4.5,
                    "image_url": "https://example.com/x.png",
                },
                "_score": 3.2,
            }],
            None,
        )

    suggestions, error = autocomplete_service.get_suggestions(
        "wireless", fetch_hits=fake_fetch_hits
    )
    assert error is None
    assert len(suggestions) == 1
    assert isinstance(suggestions[0], SuggestionItem)
    assert suggestions[0].title == "Wireless Headphones"
    assert suggestions[0].asin == "B0X"


def test_search_result_and_pagination_limit_error_are_json_safe_dataclasses():
    # Alanların tamamı primitive/list/dict olmalı (JSON-safe sözleşme).
    result = SearchResult(
        hits=[], total=0, error=None, current_page=1, page_size=20,
        total_pages=0, start_item=0, end_item=0, has_previous=False, has_next=False,
    )
    assert result.hits == []
    error = PaginationLimitError(requested_page=99, max_allowed_page=5)
    assert error.requested_page == 99
    assert error.max_allowed_page == 5
