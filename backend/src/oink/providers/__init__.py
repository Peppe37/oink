"""Linked sources of money. `PROVIDERS` are the ones set up with a key or an address;
banks go through Enable Banking (a redirect to the bank, see `enablebanking`)."""

from __future__ import annotations

from oink.providers.base import Provider, ProviderError, RemoteAccount
from oink.providers.crypto import Binance, BitcoinAddress, EthereumAddress, Kraken
from oink.providers.wise import Wise

PROVIDERS: dict[str, Provider] = {
    p.id: p for p in (Wise(), Kraken(), Binance(), BitcoinAddress(), EthereumAddress())
}

__all__ = ["PROVIDERS", "Provider", "ProviderError", "RemoteAccount"]
