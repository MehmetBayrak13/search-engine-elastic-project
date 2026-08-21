"""UI framework'ünden bağımsız arama/autocomplete servis katmanı.

Bu paket (`search_service`, `autocomplete_service`, `search_models`) hiçbir
UI framework import'u veya state yönetimi içermez; yalnızca Elasticsearch
sorgu oluşturma/çalıştırma iş mantığını taşır. `api/main.py` (FastAPI) bu
paketi çağıran ince bir HTTP katmanıdır.
"""
