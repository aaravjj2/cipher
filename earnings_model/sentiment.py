"""Financial sentiment analyzer for earnings news and headlines.

Implements a high-throughput Loughran-McDonald inspired financial sentiment
lexicon and scoring algorithm tailored to earnings releases, analyst notes,
previews, and post-earnings news.
"""
import re
import math
from typing import Dict, Any, List

# Loughran-McDonald financial dictionary token subsets
POSITIVE_WORDS = {
    'beat', 'beats', 'beating', 'beaten', 'exceeded', 'exceeds', 'exceeding',
    'surpassed', 'surpasses', 'surpassing', 'outperform', 'outperforms', 'outperformed',
    'outperforming', 'surge', 'surges', 'surged', 'surging', 'soar', 'soars',
    'soared', 'soaring', 'jump', 'jumps', 'jumped', 'jumping', 'rally', 'rallies',
    'rallied', 'rallying', 'gain', 'gains', 'gained', 'gaining', 'growth',
    'record', 'strong', 'stronger', 'strongest', 'strength', 'profit', 'profitable',
    'profitability', 'lucrative', 'bullish', 'upgrade', 'upgrades', 'upgraded',
    'upgrading', 'raise', 'raises', 'raised', 'raising', 'boost', 'boosts',
    'boosted', 'boosting', 'higher', 'highest', 'upside', 'accelerate', 'accelerates',
    'accelerated', 'accelerating', 'acceleration', 'robust', 'solid', 'resilient',
    'optimistic', 'optimism', 'dividend', 'buyback', 'expansion', 'expanding',
    'breakthrough', 'innovation', 'milestone', 'stellar', 'crushed', 'crushes'
}

NEGATIVE_WORDS = {
    'miss', 'misses', 'missed', 'missing', 'fell', 'fall', 'falling', 'falls',
    'drop', 'drops', 'dropped', 'dropping', 'tumble', 'tumbles', 'tumbled',
    'tumbling', 'plunge', 'plunges', 'plunged', 'plunging', 'slump', 'slumps',
    'slumped', 'slumping', 'decline', 'declines', 'declined', 'declining',
    'loss', 'losses', 'losing', 'lost', 'unprofitable', 'bearish', 'downgrade',
    'downgrades', 'downgraded', 'downgrading', 'lower', 'lowered', 'lowering',
    'lows', 'lowest', 'cut', 'cuts', 'cutting', 'slashed', 'slash', 'slashing',
    'weak', 'weaker', 'weakest', 'weakness', 'drag', 'drags', 'dragged',
    'headwind', 'headwinds', 'deficit', 'shrink', 'shrinks', 'shrinking',
    'shrank', 'slowdown', 'slowing', 'slowed', 'recession', 'warning', 'warns',
    'warned', 'downside', 'disappoint', 'disappoints', 'disappointed',
    'disappointing', 'disappointment', 'bleak', 'pessimistic', 'guidance cut',
    'layoffs', 'probe', 'lawsuit', 'investigation', 'delayed', 'cancellation'
}

UNCERTAINTY_WORDS = {
    'uncertain', 'uncertainty', 'volatility', 'volatile', 'risk', 'risks',
    'risky', 'cautious', 'caution', 'unclear', 'doubt', 'doubts', 'doubtful',
    'speculation', 'speculative', 'turbulent', 'turbulence', 'unpredictable',
    'hesitant', 'mixed', 'dilemma', 'crossroads', 'potential', 'contingent'
}

NEGATION_WORDS = {
    'not', "n't", 'never', 'no', 'neither', 'hardly', 'scarcely', 'barely',
    'fails', 'failed', 'failing', 'failure', 'unable', 'without'
}

INTENSIFIERS = {
    'very', 'sharply', 'substantially', 'significantly', 'massively',
    'hugely', 'dramatically', 'heavily', 'steeply', 'widely'
}


def tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase alphanumeric words."""
    if not text:
        return []
    return re.findall(r"\b[a-zA-Z']+\b", text.lower())


def score_text(text: str) -> Dict[str, Any]:
    """Analyze text and return sentiment metrics.

    Returns:
        Dict with:
          - sentiment_score: float in range [-1.0, 1.0]
          - sentiment_label: 'positive' | 'negative' | 'neutral'
          - pos_count, neg_count, unc_count, total_words
          - positive_ratio, negative_ratio, uncertainty_ratio
    """
    if not text:
        return {
            'sentiment_score': 0.0,
            'sentiment_label': 'neutral',
            'pos_count': 0,
            'neg_count': 0,
            'unc_count': 0,
            'total_words': 0,
            'positive_ratio': 0.0,
            'negative_ratio': 0.0,
            'uncertainty_ratio': 0.0,
        }

    tokens = tokenize(text)
    total_words = len(tokens)
    if total_words == 0:
        return {
            'sentiment_score': 0.0,
            'sentiment_label': 'neutral',
            'pos_count': 0,
            'neg_count': 0,
            'unc_count': 0,
            'total_words': 0,
            'positive_ratio': 0.0,
            'negative_ratio': 0.0,
            'uncertainty_ratio': 0.0,
        }

    pos_score = 0.0
    neg_score = 0.0
    unc_count = 0

    pos_count = 0
    neg_count = 0

    for i, token in enumerate(tokens):
        # Check for negation in 3 preceding tokens
        window = tokens[max(0, i - 3):i]
        is_negated = any(neg in window for neg in NEGATION_WORDS)

        # Check for intensifiers
        has_intensifier = any(inte in window for inte in INTENSIFIERS)
        multiplier = 1.5 if has_intensifier else 1.0

        if token in POSITIVE_WORDS:
            if is_negated:
                neg_score += 1.0 * multiplier
                neg_count += 1
            else:
                pos_score += 1.0 * multiplier
                pos_count += 1
        elif token in NEGATIVE_WORDS:
            if is_negated:
                pos_score += 0.8 * multiplier  # 'not bad' is mildly positive
                pos_count += 1
            else:
                neg_score += 1.0 * multiplier
                neg_count += 1
        elif token in UNCERTAINTY_WORDS:
            unc_count += 1

    # Normalized score between -1.0 and +1.0 using hyperbolic tangent scaling
    net_diff = pos_score - neg_score
    magnitude = pos_score + neg_score

    if magnitude == 0:
        raw_score = 0.0
    else:
        # Scale with word count dampening
        raw_score = net_diff / math.sqrt(magnitude + 2.0)
        raw_score = math.tanh(raw_score)

    # Classify
    if raw_score >= 0.15:
        label = 'positive'
    elif raw_score <= -0.15:
        label = 'negative'
    else:
        label = 'neutral'

    return {
        'sentiment_score': round(float(raw_score), 4),
        'sentiment_label': label,
        'pos_count': pos_count,
        'neg_count': neg_count,
        'unc_count': unc_count,
        'total_words': total_words,
        'positive_ratio': round(pos_count / total_words, 4),
        'negative_ratio': round(neg_count / total_words, 4),
        'uncertainty_ratio': round(unc_count / total_words, 4),
    }
