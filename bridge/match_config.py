"""Validate base cards against the pinned engine's public card metadata."""
import json
from .paths import ROOT

CARDS = json.loads((ROOT / 'src/data/nativeCards.json').read_text(encoding='utf-8'))
CARD_IDS = {card['id'] for card in CARDS}
FORM_MASKS = {card['id']: card['forms'] for card in CARDS}
CHAMPION_IDS = {card['id'] for card in CARDS if card['rarity'] == 'champion'}
OMITTED_STARTING_CARDS = {card['id'] for card in CARDS if card.get('omitFromStartingHand')}
STANDARD_DECK = (26000000, 26000001, 26000005, 28000001, 28000000, 26000003, 26000014, 26000018)
TOWER_CARDS = json.loads((ROOT / 'src/data/nativeTowerCards.json').read_text(encoding='utf-8'))
TOWER_IDS = {card['id'] for card in TOWER_CARDS}
DEFAULT_TOWER = 159000000


def validate_towers(tower0=None, tower1=None):
    result = tuple(DEFAULT_TOWER if tower is None else tower for tower in (tower0, tower1))
    if any(type(tower) is not int or tower not in TOWER_IDS for tower in result):
        raise ValueError('塔楼牌不受当前引擎支持')
    return result


def validate_level(level=None):
    # Missing levels retain the original standard-match template for archives.
    if level is not None and (type(level) is not int or not 1<=level<=16):
        raise ValueError('战斗等级必须为 1～16 级')
    return level


def validate_deal_mode(decks, mode=None):
    if mode not in (None,'ordered'):
        raise ValueError('初始牌序模式无效')
    if mode=='ordered':
        for deck in decks:
            count=sum(card in OMITTED_STARTING_CARDS for card in deck)
            positions=tuple(index for index,card in enumerate(deck) if card in OMITTED_STARTING_CARDS)
            expected=() if count==0 else (7,) if count==1 else (5,7)
            if positions!=expected:
                raise ValueError('原生牌序规则：单张镜像或采集器在第 8 位；同时携带时在第 6、8 位')
    return mode


def ordered_deal(state):
    return tuple(tuple(card.card for card in sorted(player.hand,key=lambda card:card.slot))+tuple(player.cycle)
                 for player in state.players)


def remap_deal(decks, forms, observed, requested):
    result_decks=[];result_forms=[]
    for deck,side_forms,actual,target in zip(decks,forms,observed,requested):
        if len(actual)!=8 or set(actual)!=set(deck):
            raise ValueError('原生引擎没有返回完整的八张初始牌序')
        by_card=dict(zip(deck,side_forms))
        remapped=list(deck)
        for dealt_card,wanted_card in zip(actual,target):
            remapped[deck.index(dealt_card)]=wanted_card
        result_decks.append(tuple(remapped))
        result_forms.append(tuple(by_card[card] for card in remapped))
    return tuple(result_decks),tuple(result_forms)


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
        evolutions = forms.count(1)
        heroes = sum(mode == 2 or card in CHAMPION_IDS for card, mode in zip(deck, forms))
        if max(0, evolutions-1) + max(0, heroes-1) > 1:
            raise ValueError('每方有一个觉醒位、一个精英位、一个万能位；精英和英雄共用精英位或万能位')
        result.append(tuple(forms))
    return tuple(result)
