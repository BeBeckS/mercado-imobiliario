# mercado-imobiliario-coleta

Componente acadêmico exclusivamente de coleta/ingestão de anúncios imobiliários, com validação e disponibilização em JSON. Não utiliza banco de dados.

## Fonte e limitações

Fonte inicial: [VivaReal, apartamentos à venda em Porto Alegre](https://www.vivareal.com.br/venda/rio-grande-do-sul/porto-alegre/apartamento_residencial/). A URL é configurável em `SOURCE_URL`.

**VivaReal:** na validação do fluxo, o `robots.txt` retornou HTTP 403 e a página de anúncios não foi requisitada. A coleta automatizada de anúncios reais não foi verificada. O parser também foi anteriormente testado offline com uma amostra de HTML salva manualmente, que não é necessária aos testes atuais; ela não demonstra permissão ou disponibilidade de acesso automatizado e sua estrutura pode mudar. A implementação VivaReal consulta apenas a página configurada.

**Barcellos:** a fonte [Barcellos Assessoria Imobiliária](https://www.barcellos.com.br/vendas) utiliza o JSON público carregado pela página, com paginação. A disponibilidade HTTP observada não substitui a avaliação dos termos de uso e da permissão para coleta automatizada. Se o acesso for negado ou a resposta for inválida, a coleta termina com erro.

## Estrutura

```text
src/
  collectors/       contrato de fonte, acesso HTTP e parsers
  models/           modelo Pydantic
  utils/            normalização, hash e persistência
  config/           configuração
tests/
  fixtures/         HTML e JSON sintéticos para testes offline
data/properties.json  imóveis consolidados (ignorado pelo Git, criado na primeira gravação)
data/snapshots/       registros e metadados de cada coleta (ignorado pelo Git)
data/raw/             dados locais protegidos; apenas .gitkeep é versionável
main.py             execução
requirements.txt    dependências
.env.example        exemplo de configuração
Dockerfile          imagem da aplicação
.dockerignore       exclusões do contexto de build
```

## Configuração e instalação

Requer Python 3.12 ou superior. No PowerShell, na raiz do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edite `.env`, configure `SOURCE_URL` para a fonte escolhida (por exemplo, uma das URLs acima) e substitua o contato de exemplo no `USER_AGENT` por uma identificação apropriada. As variáveis de ambiente já definidas têm precedência sobre `.env`.

| Variável | Finalidade | Padrão |
|---|---|---|
| `PROPERTY_SOURCE` | Fonte: `vivareal`, `local` ou `barcellos` | `vivareal` |
| `LOCAL_HTML_PATH` | Caminho do HTML local, relativo ao diretório de execução ou absoluto | Obrigatório para `local` |
| `SOURCE_URL` | URL da página de busca | Obrigatória para `vivareal` e `barcellos` |
| `USER_AGENT` | Identificação do coletor | Obrigatória |
| `REQUEST_TIMEOUT_SECONDS` | Timeout HTTP positivo, em segundos | `15` |
| `REQUEST_INTERVAL_SECONDS` | Intervalo mínimo não negativo entre requisições | `2` |
| `OUTPUT_DIR` | Diretório de saída para `properties.json` e `snapshots/` | `data` |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` ou `CRITICAL` | `INFO` |

## Execução e testes

```powershell
.\.venv\Scripts\python.exe main.py
$env:RUN_HTTP_DIAGNOSTIC = "0"
.\.venv\Scripts\python.exe -m pytest -q
```

Com o ambiente virtual ativado, também é possível executar `python main.py` e `python -m pytest -q`. Os testes padrão são offline: usam HTML e JSON sintéticos e respostas HTTP simuladas, sem depender de `.env` ou das variáveis de configuração externas. O diagnóstico HTTP real é o único teste ignorado por padrão e só realiza acesso se `RUN_HTTP_DIAGNOSTIC=1` for definido; para garantir a suíte offline mesmo quando essa variável existir no ambiente, configure-a como `0`, conforme acima.

### Execução com Docker

A imagem `mercado-imobiliario-coleta:latest` executa a coleta de forma isolada, sem depender do Python instalado localmente. Com o Docker instalado, construa a imagem:

```powershell
docker build -t mercado-imobiliario-coleta:latest .
```

Configure o arquivo `.env` e execute a coleta carregando suas variáveis com `--env-file .env`:

```powershell
docker run --rm --env-file .env -v mercado-imobiliario-data:/app/data mercado-imobiliario-coleta:latest
```

O container executa `python main.py`. O volume Docker `mercado-imobiliario-data`, montado em `/app/data`, preserva o arquivo principal `properties.json` e os snapshots após a remoção do container, permitindo seu consumo posterior por outros módulos. Para usar esse volume, mantenha `OUTPUT_DIR=data` ou configure `OUTPUT_DIR=/app/data` no `.env`; outros caminhos não serão persistidos nesse volume. Caminhos como `LOCAL_HTML_PATH` devem ser válidos dentro do container Linux. O `.env` e os dados locais não são incorporados à imagem.

#### Verificar os arquivos no volume

Após executar a coleta, liste os arquivos com o volume montado somente para leitura:

```powershell
docker run --rm --network none --mount type=volume,source=mercado-imobiliario-data,target=/app/data,readonly --entrypoint ls mercado-imobiliario-coleta:latest -laR /app/data
```

Antes de buscar dados, o coletor consulta o `robots.txt` da mesma origem. Se ele não estiver acessível, não puder ser validado ou não permitir a URL para o agente, a execução termina com erro. HTML e conteúdo incompatível são rejeitados mesmo com HTTP 200; por precaução, arquivos vazios ou apenas com comentários também são recusados por não permitirem verificar a política. O tipo HTTP por si só não determina a validade do conteúdo. Um `Crawl-delay` superior ao intervalo configurado ou uma regra `Request-rate` também interrompe a execução. HTTP diferente de 200, inclusive redirecionamentos, timeout, tipo de resposta inesperado e estrutura inválida causam erro explícito e código de saída diferente de zero. Os logs HTTP informam método, host, caminho e status sem a query ou o fragmento; erros de rede informam o tipo da exceção, não sua mensagem potencialmente sensível. O cliente limita os logs de `urllib3` a `ERROR` no processo para evitar que mensagens de depuração ou repetição exponham queries; erros de requisição continuam sendo informados pelo coletor. Não há tentativas de contornar bloqueios.

O `main.py` carrega as configurações e usa uma `PropertySource` para obter imóveis. A implementação padrão `VivaRealSource` usa o cliente HTTP existente, fecha a sessão e passa o HTML ao parser `Property`. Quando há imóveis reconhecidos, o programa salva o JSON e exibe a quantidade e o caminho do arquivo. Um HTTP 403 é informado como recusa da fonte e encerra a execução sem contornar o bloqueio; HTML inválido ou sem imóveis também encerra sem salvar.

### Arquitetura de fontes

`PropertySource` define `collect() -> list[Property]`. O `main.py` seleciona a implementação conforme `PROPERTY_SOURCE`, enquanto identidade e persistência recebem apenas `Property`. O valor padrão é `vivareal`; `local` seleciona `LocalPropertySource` e `barcellos` seleciona `BarcellosSource`. Valores vazios ou desconhecidos geram erro explícito. Para adicionar futuramente outra fonte autorizada, crie uma implementação do contrato com sua própria obtenção e parser, forneça a identificação correta nos imóveis e acrescente sua seleção à configuração e ao ponto de composição em `main.py`.

Para a Barcellos, configure `PROPERTY_SOURCE=barcellos` e `SOURCE_URL=https://www.barcellos.com.br/vendas`. A fonte deriva da origem configurada o caminho `/api/imoveis.php` e solicita `finalidade=vendas&pagina=N`. O cliente verifica as regras de `robots.txt` para cada URL, reutilizando as regras obtidas durante a execução, e mantém timeout, intervalo e bloqueio de redirecionamentos. A coleta começa na página 1 e avança enquanto `tem_mais` for verdadeiro; a última página não gera chamada adicional. Respostas inválidas, truncadas, páginas que não avançam ou sem novos identificadores interrompem a coleta sem persistência parcial. O total deve permanecer constante entre páginas e, ao terminar, coincidir com a quantidade de identificadores distintos; mudanças na quantidade durante a execução podem exigir uma nova coleta.

O parser usa `code` como identidade da fonte e a rotina compartilhada para mesclar duplicatas, mantendo os valores não nulos mais recentes. A estabilidade futura do código não é garantida. URLs relativas são normalizadas para a origem configurada; URLs de outra origem são rejeitadas. `suites`, `valor_condominio`, `valor_iptu` e `sob_consulta` não possuem campos no modelo atual. O `valor` é preservado mesmo quando `sob_consulta=true`, mas a flag não é persistida: consumidores **não devem presumir** que esse preço seja publicável.

Para testes e validação offline, configure `PROPERTY_SOURCE=local` e informe `LOCAL_HTML_PATH` no `.env`, por exemplo `tests\fixtures\imoveis_locais_sinteticos.html`. `LocalPropertySource` lê o HTML em UTF-8 e usa o mesmo parser, sem HTTP nem `SOURCE_URL`; URLs relativas permanecem relativas. `USER_AGENT` continua obrigatório na configuração compartilhada. Seus imóveis são identificados como `Fixture local`. Configure também um `OUTPUT_DIR` separado para evitar misturar dados de teste com os dados existentes. A fixture sintética contém apenas dados fictícios e não comprova a estrutura real do VivaReal. Execute os testes específicos com:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_local_property_source.py -q
```

Para usar apenas a camada HTTP, sem executar o parser:

```python
from src.collectors.http_client import HttpClient
from src.config.settings import Settings

client = HttpClient(Settings.from_env())
try:
    html = client.fetch_page()
finally:
    client.close()
```

`fetch_page()` retorna o HTML não vazio ou lança `CollectionError` em caso de falha de acesso, URL inválida ou resposta inadequada. A configuração inválida carregada por `Settings.from_env()` gera `ValueError`.

## Escopo da primeira publicação

O primeiro conjunto publicado deve conter o código e a configuração de coleta, os modelos e a persistência atualmente necessários, os testes de coleta, as fixtures sintéticas, a documentação, `.env.example`, `requirements.txt`, `.gitignore`, `Dockerfile`, `.dockerignore` e `data/raw/.gitkeep`.

Não devem entrar no primeiro commit: `.env`, `properties.json`, snapshots, dados reais, `.venv`, caches, logs e arquivos temporários. Arquivos de análise que possam existir no diretório de trabalho, `src/market_analysis.py` e `tests/test_market_analysis.py`, serão separados posteriormente para o repositório de Analytics e não fazem parte do primeiro conjunto publicado. Esses dois arquivos não são excluídos pelo `.gitignore`; a seleção para o commit deve excluí-los explicitamente.

## Requisitos para novas fontes

Uma fonte candidata deve, idealmente:

- disponibilizar acesso legítimo aos dados e permitir coleta automatizada conforme seus termos e regras aplicáveis;
- fornecer dados suficientes para o modelo `Property`, com identificador estável ou outro mecanismo confiável de identidade e, quando disponível, URL ou referência individual do imóvel;
- permitir tratar paginação quando necessária e ter estrutura suficientemente estável para manutenção;
- permitir testes offline com fixtures, sem depender de acesso à fonte;
- não exigir credenciais privadas no código nem contorno de HTTP 403, `robots.txt`, CAPTCHA, mecanismos anti-bot ou limites de requisição.

Como **critérios de escolha**, não como validações atuais, considere `preco` com `moeda`, `area_m2`, `cidade`, `tipo_imovel`, `fonte` e uma forma confiável de identificar o anúncio, preferencialmente `id_anuncio` ou `url`. `titulo`, `quartos`, `banheiros`, `vagas`, `finalidade`, `endereco`, `localizacao`, `bairro` e `estado` podem permanecer `None` quando indisponíveis ou incertos. Na implementação atual, **todos os campos de `Property` podem ser `None`**, inclusive os recomendados acima; não se deve inventar valores ausentes.

A integração deve implementar `PropertySource.collect() -> list[Property]` e avaliar a compatibilidade com a identidade compartilhada antes de persistir dados. A identidade é heurística, mas compara a fonte antes dos identificadores; uma fonte ausente não é associada a uma fonte conhecida. A seleção continua no ponto de composição de `main.py`. A paginação é específica de cada fonte; atualmente é implementada para a Barcellos.

## Diagnóstico de estrutura

Para inspecionar a página configurada sem executar o parser:

```powershell
.\.venv\Scripts\python.exe -m src.collectors.diagnostic
```

Para trabalhar offline, sem precisar de `.env` nem fazer requisições:

```powershell
.\.venv\Scripts\python.exe -m src.collectors.diagnostic --html-file tests\fixtures\diagnostico_exemplo.html
```

O relatório JSON resume candidatos de HTML, seletores indicativos, nomes relevantes de atributos `data-*`, tipos e sinais em JSON-LD ou JSON embutido. Também sinaliza possíveis mensagens de restrição no texto visível. Os candidatos e as restrições são heurísticos: contagens não representam necessariamente anúncios reais. A ausência de sinais não prova que os dados dependam de JavaScript. O diagnóstico não imprime valores dos anúncios, URLs, valores de atributos ou conteúdo bruto de scripts; também não executa JavaScript. Blocos JSON inválidos são contabilizados. A coleta remota usa o cliente existente e respeita as mesmas restrições de acesso.

Para validar somente o acesso HTTP à `SOURCE_URL`, há um teste real opt-in em `tests\test_http_diagnostic_real.py`. Ele usa o `HttpClient`, relata os códigos recebidos e o tamanho do HTML, e não salva nem interpreta a página. Em um processo PowerShell separado, configure `SOURCE_URL` e `USER_AGENT` adequados e execute:

```powershell
$env:RUN_HTTP_DIAGNOSTIC = "1"
.\.venv\Scripts\python.exe -m pytest -q -s tests\test_http_diagnostic_real.py
```

O teste fica ignorado na suíte normal quando `RUN_HTTP_DIAGNOSTIC` não é `1`. Se essa variável estiver definida como `1`, inclusive ao executar a suíte completa, o diagnóstico poderá fazer requisições reais. Falhas e restrições da fonte continuam sendo erros; a disponibilidade HTTP não comprova permissão de uso nem que o HTML contenha anúncios analisáveis.

### Diagnóstico estrutural da Barcellos

Para inspecionar a página configurada sem executar o parser ou salvar o HTML:

```powershell
.\.venv\Scripts\python.exe -m src.barcellos_diagnostic
```

O comando usa `SOURCE_URL` e `HttpClient`, respeitando as mesmas regras de acesso. Também aceita `--html-file caminho` para análise offline. O relatório contém apenas contagens, seletores e sinais estruturais, sem valores de anúncios ou URLs de API.

Na resposta de desenvolvimento de `/vendas`, o contêiner `div#lista-imoveis.grid-imoveis` estava vazio: nenhum cartão ou link individual pôde ser confirmado no HTML recebido. Não foi possível avaliar código, tipo, localização, área, quartos, suítes, banheiros, vagas, preços, condomínio, IPTU ou identificadores por imóvel, nem distinguir campos ausentes, vazios ou inválidos. Os links individuais e sua forma relativa ou absoluta também não foram observados.

O HTML continha JSON-LD `BreadcrumbList`/`ListItem` e metadados da página, não dados confirmados de imóveis. Havia configuração `window.SAMI_SITE` com chaves para APIs e `perPage` igual a 16, além de uma sentinela, indicador de carregamento e referências a `busca.js` e `scroll-infinito.js`. Esses sinais sugerem carregamento adicional e rolagem infinita, mas o JavaScript não foi executado e o funcionamento efetivo da paginação não foi confirmado. Nenhuma API ou script externo foi requisitado nessa análise.

Na investigação posterior, os dois scripts foram consultados após verificar o `robots.txt` de seus caminhos. O JavaScript indica `GET` para `https://www.barcellos.com.br/api/imoveis.php`, com `finalidade=vendas` e `pagina=1` na primeira carga. Os filtros não vazios do formulário são adicionados à query; a página seguinte usa `pagina` atual + 1 quando `tem_mais` permite. O script espera JSON com `status`, `items`, `pagina`, `tem_mais`, `total` e `filtros`. A configuração `perPage=16` controla o lote **visual**, não necessariamente a quantidade retornada pela API.

Uma única consulta controlada à primeira página, com a rota permitida pelo `robots.txt`, retornou HTTP 200 e 50 itens; `tem_mais` era verdadeiro e o total informado era 285 nessa execução. O objeto também continha `count`, `finalidade` e `truncado`. Nos itens foram observadas as chaves `code`, `tipo`, `titulo`, `bairro`, `cidade`, `estado`, `area`, `dormitorios`, `suites`, `banheiros`, `garagens`, `valor`, `valor_condominio`, `valor_iptu` e `url`, entre outras. A estabilidade da identidade, os tipos e o significado de valores ausentes ou zero ainda precisam ser avaliados; contagens podem mudar.

O diagnóstico offline `python -m src.barcellos_loading_diagnostic` aceita `--html-file`, `--busca-file`, `--scroll-file` e `--source-url` para inspecionar arquivos previamente obtidos, sem executar JavaScript ou fazer requisições. A consulta histórica usou `_get` com verificação explícita de `robots.txt`; a fonte implementada agora usa o método público `fetch_json()`, que incorpora essa verificação. `fetch_page()` continua aceitando apenas HTML.

#### Contrato JSON observado

Em uma nova validação limitada, `robots.txt` e as páginas 1 e 2 da API responderam HTTP 200. A raiz era um objeto com `status` e `finalidade` (strings), `total`, `count` e `pagina` (inteiros), `tem_mais` e `truncado` (booleanos), `filtros` (objeto) e `items` (lista de objetos). Em `filtros`, `cidades`, `bairros`, `tipos`, `caracteristicas` e `combos` eram listas; `mobiliado` era objeto e os elementos observados em `caracteristicas` tinham `valor` e `rotulo`.

Na primeira página, os 50 itens apresentaram as seguintes chaves e tipos:

| Tipo observado | Campos |
|---|---|
| String | `code`, `tipo`, `titulo`, `bairro`, `cidade`, `estado`, `condominio`, `finalidade`, `foto_capa`, `url`, `valor_formatado` |
| Inteiro | `valor`, `valor_condominio`, `valor_iptu`, `dormitorios`, `suites`, `banheiros`, `garagens` |
| Inteiro ou decimal | `area` |
| Booleano | `destaque`, `exclusivo`, `sob_consulta` |

Nenhuma dessas chaves estava ausente ou nula nessa amostra; `condominio` era string vazia em 48 itens. Isso não garante que outros resultados não contenham valores ausentes ou nulos. `valor_formatado` apresentava prefixo `R$` e formatação brasileira; todos os valores monetários, áreas e quantidades da primeira página foram convertíveis pelos normalizadores existentes. Há valores numéricos zero, cujo significado precisa ser confirmado. Um item tinha `sob_consulta=true` e `valor` diferente de zero: não se deve assumir que esse valor seja um preço publicável.

`code` era uma string numérica de quatro ou cinco dígitos, distinta nos 50 itens de cada página. As 50 URLs de cada página também eram distintas, sem sobreposição entre as duas páginas; não foi observada outra chave de ID. As URLs da primeira página eram relativas, iniciavam por `/vendas/` e seu slug começava por `v` mais o código preenchido com zeros até cinco dígitos. `code` é candidato a `id_anuncio`, mas estabilidade ao longo do tempo e colisões entre fontes ainda não foram verificadas.

Mapeamento utilizado para `Property`, sujeito a mudanças no contrato da fonte:

| API | `Property` |
|---|---|
| `code` | `id_anuncio` |
| `titulo`, `tipo`, `finalidade` | `titulo`, `tipo_imovel`, `finalidade` |
| `bairro`, `cidade`, `estado` | `bairro`, `cidade`, `estado` |
| `area`, `dormitorios`, `banheiros`, `garagens` | `area_m2`, `quartos`, `banheiros`, `vagas` |
| `valor` | `preco`, preservado inclusive quando `sob_consulta=true`; a flag não cabe no modelo |
| `valor_formatado` | sinal de `moeda=BRL` quando o prefixo `R$` estiver presente |
| `url` | `url`, normalizada em relação à origem |

`fonte` deve identificar a origem. Não há equivalente confirmado para `endereco` ou `localizacao`; mantenha `None` quando indisponível. `suites`, `valor_condominio` e `valor_iptu` não possuem campos correspondentes no modelo atual e não devem ser atribuídos a outros campos. Nesse diagnóstico limitado, foram consultadas apenas duas páginas, com 50 itens distintos em cada e `tem_mais=true`. Posteriormente, uma coleta registrou 285 imóveis em seis páginas; isso não garante que futuras respostas tenham os mesmos totais.

Para inspecionar JSON já obtido, sem rede ou valores de anúncios no relatório, use `python -m src.barcellos_contract_diagnostic --json-file caminho` e, opcionalmente, `--second-json-file caminho`. O diagnóstico não cria uma fonte de produção nem realiza consultas.

As fixtures Barcellos atuais são sintéticas. Para novos testes offline, use dados fictícios ou autorizados; não use uma amostra como prova de acesso ou permissão.

## Parser de imóveis

`parse_properties(html, base_url=None, source=None)`, em `src.collectors.property_parser`, retorna uma lista de objetos Pydantic `Property`, sem realizar requisições. Ela lê JSON-LD `Apartment` e `Product` imobiliário, JSON `SearchResults` e o objeto `window.__STATE__` presente na fixture sintética, complementando dados a partir dos cartões HTML reconhecidos. Na amostra real local, os objetos `Product` e `Apartment` são associados por `@id`, fornecendo preço e moeda; a área aceita a unidade `M2`. Vagas são associadas pelo identificador da URL do cartão ou, nos links de fragmento, pela correspondência única de endereço, preço, quartos, área e banheiros. Intervalos de vagas permanecem `None`, pois o modelo aceita apenas inteiros. URLs de lançamentos são preservadas, sem convertê-las em URLs de imóveis. Campos ausentes ou ambíguos permanecem `None`. Uma URL relativa permanece relativa se `base_url` não for fornecida. Passe `source="VivaReal"` quando a origem for conhecida.

A deduplicação usa a identidade compartilhada: identificador explícito, URL idêntica ou identificador no caminho `/imovel/<id>`; na ausência de ambos, usa hash dos campos disponíveis quando há pelo menos dois. As fontes devem coincidir, ignorando diferenças de caixa e espaços nas extremidades; fonte ausente é tratada separadamente de fonte conhecida. URLs com hosts diferentes não são consideradas equivalentes. O `@id` de JSON-LD preenche `id_anuncio` quando disponível. Esse critério é heurístico e não garante identificação de todos os duplicados. HTML vazio ou sem elementos e JSON estruturado inválido geram `PropertyParseError`; HTML válido sem imóveis retorna uma lista vazia.

O `main.py` usa esse parser; `src/models/listing.py`, `src/collectors/parser.py` e `save_listings` permanecem como legado e não fazem parte do fluxo principal. A persistência atual ainda importa `Listing`, e o parser e `save_listings` são usados por testes existentes; esses componentes devem permanecer até a revisão da persistência e das dependências. O suporte à amostra não garante compatibilidade com outras páginas ou alterações futuras da fonte.

## Formato de saída

Cada execução bem-sucedida atualiza `data/properties.json`, ou `properties.json` no diretório configurado em `OUTPUT_DIR`. O arquivo é uma lista JSON de objetos `Property`, por exemplo:

```json
{
  "id_anuncio": "12345",
  "titulo": "Apartamento no Centro",
  "preco": "450000",
  "moeda": "BRL",
  "area_m2": "85.5",
  "quartos": 2,
  "banheiros": 1,
  "vagas": 1,
  "tipo_imovel": "Apartment",
  "finalidade": null,
  "localizacao": null,
  "bairro": "Centro",
  "cidade": "Porto Alegre",
  "estado": null,
  "endereco": "Rua Exemplo, 100",
  "url": "https://example.com/imovel/apartamento-12345/",
  "fonte": "VivaReal"
}
```

Preço e área são strings decimais para preservar precisão; quantidades são inteiros. A moeda é indicada separadamente quando disponível e campos não disponíveis são `null`. O arquivo consolidado contém somente os campos de `Property`, sem data de coleta, hash persistido ou histórico de preços.

O armazenamento usa a mesma identidade do parser, incluindo a fonte. Para um imóvel existente, campos não nulos da nova coleta substituem os antigos; campos ausentes não apagam dados conhecidos e imóveis não encontrados na coleta são preservados. Sem identificador ou URL, o hash do conteúdo exige ao menos dois campos, e alterações no conteúdo podem impedir a correspondência. A identidade é heurística e não garante identificação de todos os duplicados. Identificadores conflitantes geram erro sem alterar o arquivo.

`load_properties(caminho)` retorna uma lista vazia quando o arquivo não existe e lança `PersistenceDataError` se o JSON ou seus imóveis forem inválidos. `save_properties(imoveis, caminho)` lê e valida o conteúdo anterior, consolida os dados e substitui o arquivo após uma escrita temporária no mesmo diretório. Uma falha antes da substituição preserva o arquivo anterior. A operação não oferece bloqueio entre processos: não execute gravações simultâneas no mesmo arquivo.

Cada coleta não vazia também gera, antes da atualização do consolidado, um JSON independente em `OUTPUT_DIR/snapshots/`, com nome único e os campos `data_coleta` (fim da coleta em UTC), `fonte` (valor de `PROPERTY_SOURCE`), `quantidade` e `imoveis` (lista de objetos `Property` daquela execução). Esse arquivo contém dados normalizados, não a resposta HTTP original; campos não presentes no modelo, como `sob_consulta`, não são preservados. O snapshot não inclui imóveis mantidos apenas de execuções anteriores. A escrita é atômica: se falhar, o consolidado não é atualizado. Se a atualização do consolidado falhar, o snapshot já gravado é mantido, o erro e seu caminho são registrados e a execução retorna falha.

O `properties.json` existente não é convertido em snapshot retroativo. Dados anteriores em `data/raw/` não são apagados nem importados automaticamente. O `.gitignore` protege todos os arquivos em `data/raw/`, exceto `.gitkeep`, além de `properties.json`, diretórios `snapshots/` e arquivos `anuncios_*.json` gerados em qualquer `OUTPUT_DIR` dentro do projeto. Logs, temporários, ambientes virtuais e arquivos `.env` também ficam fora do Git; `.env.example` e as fixtures sintéticas de `tests/fixtures/` permanecem versionáveis. Revise as regras se outras formas de saída forem acrescentadas.

## Cuidados com coleta automatizada

Consulte os termos de uso e as permissões aplicáveis da fonte antes de executar; o `robots.txt` não substitui essa análise. Respeite privacidade, direitos sobre conteúdo e limites de acesso. Não use CAPTCHA, login, proxy ou outras técnicas para contornar restrições. Em caso de bloqueio ou estrutura indisponível, interrompa e procure uma fonte ou API autorizada.
