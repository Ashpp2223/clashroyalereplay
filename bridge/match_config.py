"""Validate base cards against the pinned engine's public card metadata."""
import json
from .paths import ROOT

CARDS = json.loads((ROOT / 'src/data/nativeCards.json').read_text(encoding='utf-8'))
CARD_IDS = {card['id'] for card in CARDS}
FORM_MASKS = {card['id']: card['forms'] for card in CARDS}
STANDARD_DECK = (26000000, 26000001, 26000005, 28000001, 28000000, 26000003, 26000014, 26000018)


def validate_level(level=None):
    # Missing levels retain the original standard-match template for archives.
    if level is not None and (type(level) is not int or not 1<=level<=16):
        raise ValueError('战斗等级必须为 1～16 级')
    return level


def validate_decks(deck0=None, deck1=None):
    result = []
    for deck in (deck0, deck1):
        deck = list(STANDARD_DECK) if deck is None else deck
        if (not isinstance(deck, list) or len(deck) != 8 or
                any(type(card) is not int or card not in CARD_IDS for card in deck) or len(set(deck)) != 8):
            raise ValueError('每方需要八张不同的受支持普通卡牌')
        result.append(tuple(deck))
    return tuple(result)


def validate_forms(decks, forms0=None, forms1=None):
    result=[]
    for deck,forms in zip(decks,(forms0,forms1)):
        forms=[0]*8 if forms is None else forms
        if not isinstance(forms,list) or len(forms)!=8 or any(type(mode) is not int or mode not in (0,1,2) or (mode and not FORM_MASKS[card]&mode) for card,mode in zip(deck,forms)):
            raise ValueError('卡牌形态不受当前引擎支持')
        if forms.count(1)>2 or forms.count(2)>2 or sum(mode!=0 for mode in forms)>3:
            raise ValueError('每方最多两张觉醒、两张精英，特殊形态合计最多三张')
        result.append(tuple(forms))
    return tuple(result)
