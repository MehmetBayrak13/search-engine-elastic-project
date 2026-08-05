"""Streamlit'ten bağımsız arama/autocomplete servis katmanı.

Bu paket (`search_service`, `autocomplete_service`, `search_models`) hiçbir
`streamlit` import'u veya `session_state` erişimi içermez; yalnızca
Elasticsearch sorgu oluşturma/çalıştırma iş mantığını taşır. `app.py` bu
paketi çağıran ince bir UI/orkestrasyon katmanıdır. Aynı servisler ileride
bir FastAPI endpoint'i tarafından da doğrudan çağrılabilir.
"""
