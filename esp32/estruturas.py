# estruturas.py - estruturas de dados do ByteBank Access
#
# Tabela Hash: acha o cartão na hora, sem percorrer lista (busca em O(1)).
# Ring Buffer: guarda só os últimos acessos, sem crescer sem parar na memória.


class RingBuffer:
    """Fila circular de tamanho fixo.

    Quando enche, o item novo toma o lugar do mais antigo.
    """

    def __init__(self, capacidade=20):
        self.capacidade = capacidade
        self.dados = [None] * capacidade
        self.inicio = 0    # posição do item mais antigo
        self.tamanho = 0   # quantos itens existem agora

    def adicionar(self, item):
        fim = (self.inicio + self.tamanho) % self.capacidade
        self.dados[fim] = item
        if self.tamanho < self.capacidade:
            self.tamanho += 1
        else:
            # Estava cheio: o mais antigo foi sobrescrito, o início avança
            self.inicio = (self.inicio + 1) % self.capacidade

    def listar(self):
        """Devolve os itens do mais antigo para o mais novo."""
        return [self.dados[(self.inicio + i) % self.capacidade]
                for i in range(self.tamanho)]

    def __len__(self):
        return self.tamanho


class TabelaCartoes:
    """Tabela Hash (dicionário) com os cartões cadastrados.

    A chave é o UID do cartão e o valor é (nome, categoria).
    """

    def __init__(self):
        self.cartoes = {}

    def cadastrar(self, uid, nome, categoria):
        self.cartoes[uid] = (nome, categoria)

    def buscar(self, uid):
        """Devolve (nome, categoria) ou None se o cartão não existe."""
        return self.cartoes.get(uid)
