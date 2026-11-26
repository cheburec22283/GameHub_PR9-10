# Развёртывание user-service через GitHub OIDC и Yandex Lockbox

Этот документ описывает подготовленную конфигурацию. Сам по себе файл workflow
не подтверждает, что облачный контейнер уже развёрнут. В текущем файле
`.github/gamehub-cloud.json` стоит `enabled: false`.

## Настройки, которые можно хранить в репозитории

`.github/gamehub-cloud.json` содержит только несекретные идентификаторы ресурсов.
`tools/cloud_config.py` проверяет точный набор полей, JSON boolean для enabled,
формат идентификаторов и отсутствие переводов строк. При включённом деплое
скрипт передаёт фиксированный набор переменных в GITHUB_ENV.

| Поле | Назначение |
| --- | --- |
| enabled | Включать только после настройки и проверки зависимостей |
| folder_id | Каталог Yandex Cloud |
| registry_id | Реестр образов |
| deploy_service_account_id | Аккаунт, от имени которого работает CI |
| runtime_service_account_id | Аккаунт ревизии контейнера |
| container_name | Имя с фамилией в начале |
| network_id | VPC с доступом к PostgreSQL и RabbitMQ |
| lockbox_secret_id | Секрет со строками подключения |
| lockbox_version_id | Точная версия секрета; latest запрещён |
| otlp_traces_endpoint | Необязательный адрес приёма трасс без credentials |

Пароли, строки подключения, IAM-токены и авторизованные ключи в этот JSON не
добавляются. Для облачного задания GitHub Secrets и YC_KEYS не используются.
Существующий Docker Hub job сохраняет свою прежнюю конфигурацию.

## Федерация GitHub

В Yandex Identity and Access Management необходимо создать workload identity
federation и привязать к ней deploy-аккаунт.

| Поле | Значение для этого репозитория |
| --- | --- |
| Issuer | `https://token.actions.githubusercontent.com` |
| Audience федерации | `https://github.com/cheburec22283` |
| JWKS | `https://token.actions.githubusercontent.com/.well-known/jwks` |
| External subject | `repo:cheburec22283/GameHub_PR9-10:environment:yandex-cloud` |

В subject используется environment, поскольку облачный job содержит
`environment: yandex-cloud`. Subject с `ref:refs/heads/main` этому job не
соответствует. Запуск job дополнительно ограничен основной веткой и исключает
pull_request. Для environment рекомендуется разрешить развёртывание только из
основной ветки.

GitHub job имеет `permissions: contents: read, id-token: write`.
`actions/github-script` запрашивает OIDC-токен для указанного audience и
обменивает его на краткоживущий IAM-токен по официальному endpoint
`https://auth.yandex.cloud/oauth/token`. Audience запроса обмена — ID
deploy-аккаунта; это другой параметр, чем audience исходного OIDC-токена.
Токены маскируются до передачи в outputs. Один IAM-токен используется для
Container Registry, deploy action и smoke-проверки.

## Права аккаунтов

Назначать права следует на нужные ресурсы, а не на всё облако.

| Аккаунт | Роль | Область |
| --- | --- | --- |
| Deploy | container-registry.images.pusher | Реестр проекта |
| Deploy | serverless-containers.editor | Каталог с контейнером |
| Deploy | serverless-containers.containerInvoker | Контейнер для smoke-проверки |
| Deploy | iam.serviceAccounts.user | Runtime-аккаунт |
| Deploy | vpc.user | Каталог с VPC |
| Deploy | functions.editor | Каталог; требуется deploy action при подключении secrets |
| Deploy | lockbox.payloadViewer | Секрет проекта |
| Runtime | container-registry.images.puller | Реестр проекта |
| Runtime | lockbox.payloadViewer | Секрет проекта |

Если секрет зашифрован пользовательским KMS-ключом, Runtime также нужен
`kms.keys.encrypterDecrypter` на этот ключ. При явном ID секрета и версии
разрешение lockbox.viewer для поиска по имени/по latest не требуется.
В workflow задано `public: false`; права allUsers не нужны.

Создание федерации требует у настраивающего пользователя роли
`iam.workloadIdentityFederations.editor`. Создание связи требует
`iam.serviceAccounts.federatedCredentialEditor` на deploy-аккаунт и
`iam.workloadIdentityFederations.user` на каталог федерации. Самому CI эти роли
для обычного деплоя не нужны.

## PostgreSQL, RabbitMQ и Lockbox

Зависимости должны быть доступны из выбранной VPC. Для учебной базы нужен
отдельный пользователь PostgreSQL и отдельная база user-service. Строки
подключения размещаются в Yandex Lockbox как значения ключей `POSTGRES_URL`
и `AMQP_URL`. Их имена отображаются в `revision-secrets`; сами значения не
попадают в репозиторий или GitHub Secrets.

Перед включением деплоя нужно записать network_id, lockbox_secret_id и точный
lockbox_version_id. Наличие JSON-конфигурации не создаёт эти ресурсы.

## Проверка результата

После тестов и E2E конвейер собирает образ user-service, загружает его в реестр
с тегом SHA и создаёт приватную ревизию с timeout 60 секунд и выбранной VPC.
`tools/cloud_smoke.py` получает фактический URL через Container.Get и проверяет:

1. Отказ 401/403 при вызове без IAM-токена.
2. Успешный авторизованный `/health`.
3. Регистрацию синтетического пользователя и чтение сохранённой записи.
4. Наличие пользователя в списке с пагинацией.
5. Наличие HTTP-метрик.

Проверка оставляет одну синтетическую запись, так как API удаления не реализован.
Токен не выводится и не передаётся в URL; HTTP-перенаправления запрещены.
GitHub summary фиксирует контейнер, ревизию и образ только после успешного smoke.

У user-service нет подписок RabbitMQ, поэтому его `/health` не проверяет
соединение с брокером. Также код логирует, но не пробрасывает ошибку публикации
outbox. Поэтому доставка `user.registered` проверяется отдельно по outbox или
тестовой очереди; один ответ 201 её не подтверждает.

## Первичные источники

- https://yandex.cloud/en/docs/tutorials/serverless/ci-cd-github-functions
- https://yandex.cloud/en/docs/iam/operations/wlif/setup-wlif
- https://yandex.cloud/en/docs/serverless-containers/operations/auth
- https://github.com/yc-actions/yc-sls-container-deploy/blob/v5/action.yml
- https://github.com/yc-actions/yc-sls-container-deploy/blob/v5/README.md
- https://github.com/yc-actions/yc-iam-token-fed/blob/main/main.go
