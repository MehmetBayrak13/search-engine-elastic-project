"""FastAPI backend — React frontend'in tükettiği HTTP API.

Bu paket hiçbir arama/autocomplete iş mantığı içermez; tamamı
`services/search_service.py` ve `services/autocomplete_service.py`da
yaşar (bkz. o modüllerin docstring'i). Burada yalnızca HTTP sözleşmesi
(request/response şekli), CORS ve önbellekleme (bkz. `api/cache.py:
ttl_cache`) yaşar.
"""
