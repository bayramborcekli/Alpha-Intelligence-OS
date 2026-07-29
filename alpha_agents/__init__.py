"""Alpha Intelligence OS kalıcı sistem agent'ları.

Import ağır işlem BAŞLATMAZ; agent'lar yalnız açıkça çağrıldığında
çalışır. Kayıt/durum için registry.list_agents() kullanılır.
"""
from alpha_agents.registry import get_agent, list_agents  # noqa: F401
