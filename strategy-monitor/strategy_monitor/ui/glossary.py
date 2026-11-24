"""Significado dos termos usados na tela (fonte única: tooltips, popover de glossário). Mantenha em sincronia com
a seção "Glossário" do README."""

GLOSSARY: dict[str, str] = {
    "R": "Unidade de risco de um trade: a distância da entrada até o stop inicial, em dinheiro (distância × volume). "
         "Ganhar o dobro do que arriscava = +2 R; levar o stop cheio = −1 R.",
    "E[R]": "Expectância: a média do resultado por trade, em R. +0,125 R = em média cada trade ganha 12,5% do que "
            "arrisca. Positivo ganha em média, negativo perde. Não depende do tamanho do lote, por isso é o objetivo.",
    "IS": "In-sample: a janela em que a busca testa as combinações. Qualquer parâmetro pode parecer bom aqui por "
          "sorte, porque foi escolhido olhando esses dados.",
    "OOS": "Out-of-sample: a janela mais recente, que a busca nunca viu. Só os finalistas são avaliados nela; é a "
           "prova de que o resultado vale fora dos dados em que foi escolhido.",
    "Trechos": "O IS dividido em pedaços iguais. Um edge de verdade costuma ser positivo em mais de um pedaço; "
               "um que vive num só é suspeito.",
    "Vizinhança": "Mover um parâmetro por vez para o valor vizinho da grade. Se os vizinhos são ruins, o finalista "
                  "é um pico isolado (sorte). Vizinho que não muda o E[R] (como trocar só o lote) não conta.",
    "Referência": "Os inputs do baseline, sem alteração, rodados na mesma janela. Um candidato precisa superá-la "
                  "no OOS.",
    "n": "Número de trades da janela. Poucos trades = resultado barulhento.",
    "DD": "Drawdown máximo do saldo de trades fechados, em % do pico. É o que o teto de drawdown limita.",
    "Lote no teto": "Com o dimensionamento no final, o maior lote fixo cujo drawdown fica dentro do teto, na janela "
                    "inteira (IS + OOS). O lote muda o DD e o dinheiro, não o E[R].",
    "Viés de seleção": "Testar muitas combinações e ficar com a melhor infla o resultado mesmo sem edge algum. Por "
                       "isso o número que vale é o do OOS.",
    "Baseline": "O backtest aprovado de uma estratégia × ativo (um .xlsx do Tester): a régua do monitor.",
    "Breakeven": "Win rate em que a expectativa em dinheiro zera: perda média ÷ (ganho médio + perda média). Win "
                 "rate acima dele = estratégia ganha em média.",
    "IC95": "Intervalo de confiança de 95% do win rate (Wilson). Com poucos trades ele é largo: o win rate "
            "verdadeiro pode estar em qualquer ponto dele.",
    "PF": "Profit factor: ganhos totais ÷ perdas totais. Acima de 1 = lucrativo.",
}


def tip(*terms: str) -> str:
    """Texto de tooltip (`help=`) com o significado dos termos citados."""
    return "\n\n".join(f"{t}: {GLOSSARY[t]}" for t in terms)
