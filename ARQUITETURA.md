# Integração Domínio ↔ SEFAZ-BA: arquitetura (fase de projeto)

Status: projeto, nada implementado. Itens marcados **[VALIDAR]** dependem de confirmação em documentação oficial antes de codificar.

## 1. Objetivo

Software que obtém XML de NF-e/NFC-e na SEFAZ-BA, gera o PDF (DANFE) quando necessário e envia os documentos ao escritório contábil pela API Domínio.

## 2. Fontes de dados e autenticação

### 2.1 SEFAZ-BA (acesso somente por certificado)
- Autenticação: certificado digital e-CNPJ do cliente (A1), usado em mTLS nos webservices.
- Não armazenar usuário e senha de portal no escopo padrão. Se um caso específico exigir, solicitar no momento do uso, sem persistir (ver seção 6).
- Webservices SOAP (.asmx) de NF-e citados na busca: status do serviço, recepção de evento e consulta de protocolo. **[VALIDAR]** endereços de homologação e produção na página oficial (não abriu durante a pesquisa).
- Download de XML de notas emitidas contra o CNPJ do cliente: distribuição DF-e nacional (`NFeDistribuicaoDFe`, operação `nfeDistDFeInteresse`).
  - **Mensagem de entrada** (`distDFeInt`, versão 1.01, NT 2014.002 v1.40, seção 3.1): `versao`, `tpAmb` (1=produção, 2=homologação), `cUFAutor` (opcional), `CNPJ` ou `CPF` do interessado e **um** dos grupos: `distNSU/ultNSU` (sequencial, principal para download em lote), `consNSU/NSU` (NSU específico, para fechar lacuna) ou `consChNFe/chNFe` (chave de 44 dígitos).
  - **Mensagem de retorno** (`retDistDFeInt`, seção 3.2): `cStat`, `xMotivo`, `dhResp`, `ultNSU`, `maxNSU` e `loteDistDFeInt` com até 50 `docZip` (base64 gzip), cada um com `NSU` e `schema`.
  - **Regras que afetam o coletor** (seção 3.11.4): continuar sempre a partir do `ultNSU` retornado; `cStat=137` (nenhum documento) exige aguardar 1 hora; consultar fora de sequência ou antes de 1 hora gera `656` (bloqueio do CNPJ por 1 hora); consultas por chave ou NSU têm limite de 20 por hora; NSU mais antigo que cerca de 3 meses não é retornado; chave/NSU cobrem até 90 dias retroativos.
  - **Não localizado** nas seções lidas: exigência de ciência da operação antes do download. Conferir no MOC ou no XSD `retDistDFeInt_v1.01.xsd`.
  - **Schemas oficiais validados** (pacote PL_NFeDistDFe_104, em `schemas/PL_NFeDistDFe_104/`):
    - `distDFeInt_v1.01.xsd`: `versao` fixo em `1.01`; `tpAmb` obrigatório (1 produção, 2 homologação); `cUFAutor` opcional; `CNPJ` **ou** `CPF` obrigatório; **exatamente um** de `distNSU/ultNSU`, `consNSU/NSU` ou `consChNFe/chNFe`. NSU tem 15 dígitos; chave tem 44 caracteres.
    - `retDistDFeInt_v1.01.xsd`: `tpAmb`, `verAplic`, `cStat`, `xMotivo`, `dhResp`, `ultNSU`, `maxNSU` obrigatórios; `loteDistDFeInt` opcional, com até 50 `docZip` (base64, atributos `NSU` opcional e `schema` obrigatório).
    - Também no pacote: `resNFe_v1.01.xsd`, `resEvento_v1.01.xsd`, `tiposDistDFe_v1.01.xsd`.
    - Link oficial do pacote (Portal da NF-e, Documentos > Esquemas XML): https://www.nfe.fazenda.gov.br/portal/exibirArquivo.aspx?conteudo=IzuP2y0G6hk=
- NFC-e BA: não há documentação oficial de download localizada. **[VALIDAR]** com a SEFAZ-BA.

### 2.2 API Domínio (Thomson Reuters)
- Fonte: [Central do Desenvolvedor](https://www.dominiosistemas.com.br/lp-centraldodesenvolvedor-api/) e [doc 8476 do suporte](https://suporte.dominioatendimento.com/central/faces/solucao.html?codigo=8476).
- Token: `POST https://auth.thomsonreuters.com/oauth/token`, `grant_type=client_credentials`, `audience` fornecido pela Domínio, Basic Auth com `client_id`/`client_secret`.
- Fluxo (doc 8476, confirmado na leitura pública):
  1. Conferir a Key do cliente: `GET https://api.onvio.com.br/dominio/integration/v1/activation/info` (`Authorization: Bearer <token>`). Conferir contabilidade, CNPJ e nome do cliente.
  2. Gerar a Integration Key: `POST https://api.onvio.com.br/dominio/integration/v1/activation/enable` (Bearer). A resposta traz `integrationKey`.
  3. Enviar XML: `POST https://api.onvio.com.br/dominio/invoice/v3/batches`, headers `x-integration-key` e `Authorization: Bearer`, body `form-data` com o campo `query` = `{"boxeFile": false}` (application/json) e os arquivos.
  4. Consultar o envio: `GET https://api.onvio.com.br/dominio/invoice/v3/batches/{idEnvio}`, para confirmar o armazenamento.
- Key do cliente: fornecida pelo contador, uma por CNPJ, sem vencimento.
- Tipos integrados: NF-e, NFC-e, NFS-e, CF-e, CT-e.
- Token gerado uma vez por dia (validade de 24h), reutilizado em todos os envios.
- Limite: acima de 40 requisições o ERP pode ser bloqueado. Usar limitador de taxa configurável.
- TLS 1.2/1.3 com cipher suites exigidos pela documentação.
- Endpoints de envio e consulta: localizados (ver fluxo acima). **Ainda [VALIDAR]:** homologação, códigos de retorno e se software de terceiros pode obter a Integration Key ("chaves liberadas são de uso EXCLUSIVO do ERP").

## 3. Componentes

1. **Agendador**: dispara coleta por cliente e por janela de tempo.
2. **Coletor SEFAZ**: usa o certificado do cliente, consulta DF-e e baixa XMLs. Registra NSU para evitar reprocessamento.
3. **Armazenamento bruto**: guarda o XML original, sem alteração, indexado pela chave de acesso de 44 dígitos.
4. **Validador/parser**: confere a estrutura do XML (procNFe/procNFCe) e extrai os campos necessários.
5. **Gerador de DANFE (PDF)**: gera o PDF a partir do XML quando o destino exigir.
6. **Enviador Domínio**: obtém/renova o token, monta a Integration Key e envia com limitador, retry com backoff e idempotência pela chave de acesso.
7. **Monitor/auditoria**: status por documento, log de cada envio e alerta de falha ou vencimento de certificado.

## 4. Fluxo principal

1. Agendador aciona o coletor para o cliente.
2. Coletor autentica com o certificado e consulta DF-e a partir do último NSU.
3. Para cada documento: verifica ciência da operação [VALIDAR], baixa o XML e grava o bruto.
4. Parser valida e extrai os dados. Documento inválido vai para fila de erro, sem envio.
5. Gerador produz o PDF, se necessário.
6. Enviador manda ao Domínio com token válido e Integration Key. Resposta gravada.
7. Em erro, retry com backoff. Documento já enviado não é reenviado (idempotência).

## 5. Dados e segurança

- Certificado A1: arquivo `.pfx` e senha cifrados em repositório de segredos (KMS ou cofre). Acesso só do serviço coletor.
- Tokens da Domínio: cache em memória ou cofre, nunca em log.
- Logs sem XML completo, sem senha do certificado e sem token.
- Vencimento do certificado com alerta antecipado.
- Isolamento por cliente (tenant): dados e certificados separados.

## 6. LGPD

- Minimização: guardar só o necessário para o envio e a auditoria.
- Credenciais de portal, se algum dia for exigido: solicitadas no momento do uso, sem persistência. Esta decisão depende do levantamento da seção 7.
- Retenção definida por política, com exclusão quando o cliente encerrar o contrato.
- Registro das operações de tratamento e papel do encarregado (DPO) a definir.

## 7. Lacunas e pontos a validar

| # | Item | Onde validar |
|---|------|--------------|
| 1 | Endereços SOAP da SEFAZ-BA para NF-e. Produção `nfe.sefaz.ba.gov.br` confirmada com certificado e-CNPJ do cliente: `NFeStatusServico4` responde `200 OK` via mTLS (sem certificado: `403`). WSDL salvo em `wsdl/NFeStatusServico4.wsdl`: operação `nfeStatusServicoNF`, SOAP 1.1 e 1.2, endereço `https://nfe.sefaz.ba.gov.br/webservices/NFeStatusServico4/NFeStatusServico4.asmx`. Também lidos com o mesmo certificado: `NFeConsultaProtocolo4` (operação `NFeConsultaNF`, para consulta por chave) e `NFeRecepcaoEvento4` (operação `NFeRecepcaoEvento`, para eventos como ciência da operação), ambos em `nfe.sefaz.ba.gov.br`. Distribuição DF-e está no host nacional `https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx` (`200`, WSDL salvo em `wsdl/NFeDistribuicaoDFe_nacional.wsdl`), operação `nfeDistDFeInteresse`, SOAP 1.2 (e 1.1 a confirmar). Não está no host da SEFAZ-BA (`404`). Homologação `hnfe.sefaz.ba.gov.br` retornou `000` (falha de conexão), ainda não verificada. | Confirmar host da distribuição DF-e e acesso à homologação |
| 2 | Endereço e regras de distribuição DF-e e exigência de ciência da operação | Manual da NF-e (MOC) |
| 3 | Serviço de download de NFC-e na Bahia | SEFAZ-BA |
| 4 | Homologação, códigos de retorno da Domínio e elegibilidade de software de terceiros para obter a Integration Key (endpoints já localizados, seção 2.2) | Suporte/contato de integração da Domínio |
| 5 | Limite exato de 40 requisições (por minuto, por dia ou simultâneas) | Domínio |
| 6 | Quais serviços exigiriam login e senha de portal, se algum | Levantamento de casos de uso |

## 8. Próximos passos sugeridos

1. Fechar as lacunas 1 a 4.
2. Prototipar o coletor com certificado de homologação, sem envio ao Domínio.
3. Prototipar o envio ao Domínio em ambiente de teste, com token e Integration Key.
4. Só então definir o agendamento e a operação em produção.

## 9. Planejamento do coletor DF-e

### 9.1 Objetivo
Para cada CNPJ cadastrado, baixar em sequência todos os documentos de interesse (`nfeDistDFeInteresse`) a partir do último NSU processado, gravar o XML bruto e enfileirar para parsing e envio ao Domínio.

### 9.2 Entradas por CNPJ
- CNPJ do interessado (14 dígitos) e `tpAmb` (1 produção, 2 homologação).
- Certificado A1 do cliente, lido do cofre a cada ciclo. Senha do `.pfx` também no cofre.
- `cUFAutor=29` (opcional, conforme schema).

### 9.3 Estado persistido (uma linha por CNPJ)
| Campo | Uso |
|-------|-----|
| `ultimo_nsu` | Último NSU processado com sucesso. Base do próximo `distNSU/ultNSU`. |
| `max_nsu` | `maxNSU` da última resposta. |
| `proxima_consulta_apos` | Timestamp. Enforça pausa de 1 hora (cStat 137, ultNSU igual ao maxNSU, ou 656). |
| `status` | `ativo`, `bloqueado_656`, `certificado_vencido`, `erro`. |
| `ultima_consulta_em`, `ultimo_cstat`, `ultimo_xmotivo` | Auditoria e diagnóstico. |

### 9.4 Ciclo de coleta (por CNPJ)
1. Se `agora < proxima_consulta_apos`, encerrar o ciclo.
2. Montar `distDFeInt` com `distNSU/ultNSU = ultimo_nsu` e validar contra `distDFeInt_v1.01.xsd`. Falha de validação interrompe o ciclo sem chamar o serviço.
3. Chamar `nfeDistDFeInteresse` com mTLS e timeout definido.
4. Validar o retorno contra `retDistDFeInt_v1.01.xsd`. Retorno inválido não avança estado.
5. Tratar pelo `cStat`:
   - **138 (localizados):** para cada `docZip`, decodificar (base64, depois gunzip), gravar o XML bruto em `NSU + schema` e registrar o NSU. Só depois de gravar o lote inteiro, atualizar `ultimo_nsu` para o `ultNSU` do retorno (commit transacional). Se `ultNSU < maxNSU`, repetir a partir do novo `ultNSU`. Parar ao atingir `ultNSU == maxNSU`.
   - **137 (nenhum documento) ou `ultNSU == maxNSU`:** gravar `ultimo_nsu`, definir `proxima_consulta_apos = agora + 1h`.
   - **656 (consumo indevido):** definir `status = bloqueado_656`, `proxima_consulta_apos = agora + 1h`, registrar `xMotivo`. Não reenviar.
   - **Outro cStat:** não avançar NSU, registrar alerta e aplicar backoff limitado. Após N tentativas, `status = erro` e notificar.
6. Registrar auditoria de cada chamada (data, cStat, ultNSU, maxNSU, quantidade de docs). Nunca registrar XML completo nem credenciais.

### 9.5 Regras de concorrência e cadência
- **Uma execução por CNPJ por vez**, com lock. A NT 2014.002 exige que todas as aplicações do mesmo ator sigam a mesma sequência de NSU. Duas consultas paralelas para o mesmo CNPJ geram uso indevido.
- **Cadência sugerida:** um ciclo por hora por CNPJ, distribuído ao longo do tempo para não concentrar carga. **[DECISÃO]** a confirmar.
- Ao iniciar, consultar com `ultimo_nsu` persistido. Nunca usar `000000000000000` em rotina, só na primeira carga, e mesmo assim respeitar a pausa se a resposta for 137.

### 9.6 Lacunas de NSU
- Ao detectar salto no NSU (documentos recebidos fora de ordem ou NSU ausente no lote), registrar a lacuna.
- Preencher com `consNSU/NSU` para cada NSU faltante. Usar só quando houver lacuna comprovada, porque consultas por NSU têm limite de 20 por hora.

### 9.7 Consulta pontual por chave
- `consChNFe/chNFe` para reprocessar uma nota específica. Limite de 20 consultas por hora, por CNPJ. Não usar como rotina de coleta.

### 9.8 Segurança e falhas
- Certificado vencido ou não encontrado: `status = certificado_vencido`, alerta ao responsável, nenhuma chamada ao serviço.
- Certificado e senha só em memória durante o ciclo, sem gravação em disco fora do cofre.
- Verificação TLS sempre ativa.

### 9.9 Critérios de aceite
- `ultimo_nsu` não avança se a gravação do lote falhar.
- Reprocessar o mesmo lote não duplica documentos (dedup por NSU).
- Após 137, 656 ou `ultNSU == maxNSU`, nenhuma chamada antes de `proxima_consulta_apos`.
- Consulta nunca sai fora da sequência de `ultimo_nsu`.
- Retorno que falha no XSD não altera o estado.
- Hash do XML gravado confere com o XML decodificado do `docZip`.
- Logs não contêm XML completo, senha ou token.

### 9.10 Testes (sem rede)
- Usar as respostas reais já salvas em `schemas/testes/` (`resposta_distdfe.xml`, `resposta_ultNSU398.xml`, `resposta_ultNSU448.xml`) para testar gravação, dedup e avanço de NSU.
- Simular respostas 137, 656 e retorno inválido para testar a pausa e o não avanço de estado.
- Nenhum teste de 656 contra o serviço real.

### 9.11 Pontos em aberto do coletor
| # | Item | Impacto |
|---|------|---------|
| 1 | Exigência de ciência da operação antes do download | Define se o coletor recebe notas de fornecedores ou só as ciências registradas. Validar no MOC. |
| 2 | Cadência por CNPJ (sugestão: 1 por hora) | Afeta custo, atraso e risco de bloqueio. |
| 3 | Onde guardar o XML bruto (sistema de arquivos ou banco) e prazo de guarda | Definir com o contador do cliente. |
| 4 | Estratégia de detecção de lacuna de NSU | Define quando usar `consNSU`. |

## 10. Armazenamento dos XMLs (planejamento)

### 10.1 Princípios
- XML bruto é imutável: gravado uma vez, nunca sobrescrito nem editado.
- Cada documento tem um registro no banco e um arquivo no disco, vinculados por NSU e pelo SHA-256 do conteúdo.
- Fora do repositório e fora de `~/Documents`, `~/Desktop` e iCloud (regra de `~/Projetos`).

### 10.2 Camada 1: XML bruto por NSU (fonte da verdade)
- Caminho: `<raiz>/<cnpj>/nsu/<NSU15>_<schema>.xml` (ex.: `11222333000181/nsu/000000000000399_resEvento_v1.01.xml`).
- Gravação atômica: escrever em `.tmp`, calcular SHA-256, fazer `rename`.
- Permissões: diretórios `0700`, arquivos `0600`.
- Mantém a sequência completa de NSU, o que permite provar lacunas e reprocessar.

### 10.3 Camada 2: visão por chave de acesso
- Caminho: `<raiz>/<cnpj>/chave/<AAAA-MM>/<chave44>_<tipo>.xml` para `procNFe` e `resNFe`.
- Link simbólico ou cópia apontando para a camada 1; a camada 1 é a referência.
- Eventos (`resEvento`, `procEventoNFe`) ficam na pasta da chave, como `<chave44>_evento_<tpEvento>_<nSeqEvento>.xml`.

### 10.4 Metadados (PostgreSQL, como nos demais projetos)
Tabela `dfe_documento`:
- `cnpj`, `nsu` (chave primária composta)
- `schema`, `chave_nfe`, `tipo_evento` (nulo quando não há)
- `caminho_bruto`, `sha256`, `tamanho_bytes`
- `recebido_em`, `status_envio_dominio`, `tentativas_envio`

Tabela `dfe_controle` (uma linha por CNPJ):
- `ultimo_nsu`, `max_nsu`, `ultima_consulta_em`, `proxima_consulta_permitida_em`, `ultimo_cstat`

A regra de dedup é `(cnpj, nsu)` único. Mesmo chave pode aparecer em mais de um NSU, por exemplo `resNFe` seguido de `procNFe`; isso não é erro.

### 10.5 Backup, restauração e retenção
- Local: **VPS** (decidido). Backup diário incremental do diretório bruto e do banco, em um segundo local.
- Teste de restauração mensal, comparando SHA-256 com o banco.
- Retenção: **sem prazo definido por enquanto.** O coletor não apaga nada automaticamente. Os XMLs ficam guardados como cofre de armazenamento para consultas futuras. Quando houver prazo definido, a limpeza será uma rotina separada e auditada.

### 10.6 LGPD
- XML contém CPF de destinatário e dados de terceiros. Acesso restrito ao serviço coletor e a quem o cliente autorizar.
- Criptografia do disco da VPS e cofre para o certificado e a senha.
- Relatórios e logs usam CNPJ e NSU, nunca o conteúdo do XML.

### 10.7 Execução em Docker
- Serviços: `coletor` (agendador e consulta DF-e), `postgres` e `backup` (cópia diária).
- Volume nomeado para o diretório bruto, montado somente no `coletor` e no `backup`.
- Certificado `.pfx` e senha entram como Docker secrets (arquivos em `/run/secrets`), nunca em variável de ambiente nem na imagem.
- Imagem sem a senha; o `.env` fica fora do repositório.
- Verificação TLS ativa dentro do container (sem `-k`). A cadeia ICP-Brasil e da SEFAZ-BA entra na imagem por arquivo.
- Usuário não-root no container; diretório bruto com permissão `0700`.

### 10.8 Pontos em aberto
| # | Item | Quem decide |
|---|------|-------------|
| 1 | Prazo de retenção, quando for definido | Contador do cliente |
| 2 | Se `resNFe` sem `procNFe` deve ser guardado separado ou só como metadado | Regra de ciência (seção 11) |

## 11. Regra de ciência da operação

Fonte: NT 2014.002 v1.40, seção 3 (arquivo local `~/Downloads/NT2014_002_v1.40.txt`).

- Os documentos ficam disponíveis para distribuição por **até 90 dias** após a recepção pelo Ambiente Nacional.
- Sem manifestação do destinatário (Ciência da Operação, Operação não Realizada ou Confirmação), a consulta devolve **só o resumo** (`resNFe`). O XML completo (`procNFe`) não é liberado.
- O evento de cancelamento é liberado mesmo sem manifestação.
- Depois da manifestação, o Ambiente Nacional gera um NSU para liberar o download da NF-e. A exceção é o "Desconhecimento da operação", que não libera a NF-e.
- A NT **não torna a Ciência obrigatória**. A obrigatoriedade de manifestar, com prazos, vem da legislação estadual e do Ajuste SINIEF 07/05, e não foi validada aqui. Fontes de terceiros citam prazos de 10 e 180 dias; **não confirmados**.

### 11.1 Impacto no coletor
- Sem manifestação, o coletor recebe só `resNFe`. Isso serve para uma lista de notas pendentes de ciência.
- Para baixar o XML completo, o cliente precisa registrar a Ciência, que é um evento enviado à SEFAZ em nome dele. É **ação com efeito fiscal**: não pode ser automática sem autorização do cliente, por escrito, para cada tipo de manifestação.
- Decisão de projeto: o coletor **não envia manifestação por padrão**. Ele apenas lista as notas pendentes. O envio da Ciência fica como ação separada, com confirmação do cliente.
- Duas consequências de prazo: o coletor deve capturar `resNFe` dentro da janela de 90 dias, e notas mais antigas que isso não voltam pela distribuição.
