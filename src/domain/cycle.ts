import type { BoardSnapshot, CardData, PlayerCycleState, Side } from "./types.ts";

export function roundCostDelta(value: number): number {
  return Math.round(value * 10) / 10;
}

export function playCardInCycle(
  state: PlayerCycleState,
  cardId: string
): PlayerCycleState | null {
  const handIndex = state.hand.indexOf(cardId);
  if (handIndex < 0 || state.drawQueue.length === 0) {
    return null;
  }

  const nextHand = [...state.hand];
  const nextQueue = [...state.drawQueue];
  const nextCard = nextQueue.shift();
  if (!nextCard) {
    return null;
  }

  nextHand[handIndex] = nextCard;
  nextQueue.push(cardId);

  return {
    hand: nextHand,
    drawQueue: nextQueue
  };
}

export function applyPlayCardToSnapshot(
  snapshot: BoardSnapshot,
  side: Side,
  card: CardData
): BoardSnapshot | null {
  const current = side === "my" ? snapshot.myState : snapshot.enemyState;
  const nextCycle = playCardInCycle(current, card.id);
  if (!nextCycle) {
    return null;
  }

  const costDeltaChange = side === "my" ? -card.cost : card.cost;
  const nextSnapshot: BoardSnapshot = {
    ...snapshot,
    costDelta: roundCostDelta(snapshot.costDelta + costDeltaChange)
  };

  if (side === "my") {
    nextSnapshot.myState = nextCycle;
  } else {
    nextSnapshot.enemyState = nextCycle;
  }

  return nextSnapshot;
}
