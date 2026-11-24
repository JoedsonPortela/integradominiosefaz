#!/bin/zsh
# Consulta DF-e (nfeDistDFeInteresse) em HOMOLOGAÇÃO (tpAmb=2).
# Uso: zsh prototipo/consulta_dfe_homologacao.sh <envelope.xml> <saida.xml>
# A senha do .pfx é lida sem eco e não fica em arquivo nem no histórico.
# Não usa -k: a verificação TLS fica ativa.

set -eu

ENVELOPE="${1:?informe o envelope XML}"
SAIDA="${2:?informe o arquivo de saída}"
PFX="$HOME/Downloads/certificado digital rdi.pfx"
URL="https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"

# Garante que o envelope é tpAmb=2 antes de enviar
if ! grep -q '<tpAmb>2</tpAmb>' "$ENVELOPE"; then
  echo "Bloqueado: o envelope não está com tpAmb=2 (homologação)." >&2
  exit 1
fi

printf 'Senha do certificado: ' >&2
read -s SENHA
echo >&2

HTTP_CODE=$(curl -sS --max-time 60 \
  --cert-type P12 --cert "$PFX" --pass "$SENHA" \
  -H 'Content-Type: application/soap+xml; charset=utf-8' \
  --data-binary "@$ENVELOPE" \
  -o "$SAIDA" -w '%{http_code}' "$URL") || HTTP_CODE="000"

unset SENHA
echo "HTTP $HTTP_CODE -> $SAIDA"
