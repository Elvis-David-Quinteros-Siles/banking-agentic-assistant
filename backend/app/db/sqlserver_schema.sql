-- Core bancario simulado en SQL Server (CORE_BACKEND=sqlserver).
-- Lo ejecuta backend/scripts/init_sqlserver.py con un usuario administrador. Idempotente.
--
-- Seguridad (mínimo privilegio): la aplicación se conecta con el login de aplicación, que NO
-- tiene SELECT/INSERT sobre las tablas. Solo puede EJECUTAR los procedimientos de dbo, que
-- validan la pertenencia de cada cuenta al cliente dentro de la base de datos.
-- Los lotes se separan con GO (como en sqlcmd / SSMS).

IF OBJECT_ID('dbo.customers', 'U') IS NULL
CREATE TABLE dbo.customers (
    customer_id   NVARCHAR(10)  NOT NULL PRIMARY KEY,
    full_name     NVARCHAR(120) NOT NULL,
    doc_id_masked NVARCHAR(20)  NOT NULL,
    segment       NVARCHAR(20)  NOT NULL,
    created_at    DATE          NOT NULL
);
GO

IF OBJECT_ID('dbo.accounts', 'U') IS NULL
CREATE TABLE dbo.accounts (
    account_id   NVARCHAR(20)  NOT NULL PRIMARY KEY,
    customer_id  NVARCHAR(10)  NOT NULL REFERENCES dbo.customers(customer_id),
    account_type NVARCHAR(30)  NOT NULL,
    currency     CHAR(3)       NOT NULL CHECK (currency IN ('BOB', 'USD')),
    balance      DECIMAL(18,2) NOT NULL,
    status       NVARCHAR(20)  NOT NULL,
    opened_at    DATE          NOT NULL
);
GO

IF OBJECT_ID('dbo.transactions', 'U') IS NULL
CREATE TABLE dbo.transactions (
    tx_id       NVARCHAR(20)  NOT NULL PRIMARY KEY,
    account_id  NVARCHAR(20)  NOT NULL REFERENCES dbo.accounts(account_id),
    posted_at   DATETIME2(0)  NOT NULL,
    description NVARCHAR(200) NOT NULL,
    amount      DECIMAL(18,2) NOT NULL,
    currency    CHAR(3)       NOT NULL,
    channel     NVARCHAR(30)  NOT NULL,
    merchant    NVARCHAR(120) NULL
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_tx_account_date')
CREATE INDEX ix_tx_account_date ON dbo.transactions(account_id, posted_at DESC);
GO

IF OBJECT_ID('dbo.complaints', 'U') IS NULL
CREATE TABLE dbo.complaints (
    complaint_id NVARCHAR(20)   NOT NULL PRIMARY KEY,
    customer_id  NVARCHAR(10)   NOT NULL REFERENCES dbo.customers(customer_id),
    thread_id    NVARCHAR(64)   NOT NULL,
    category     NVARCHAR(30)   NOT NULL,
    product      NVARCHAR(40)   NULL,
    amount       DECIMAL(18,2)  NULL,
    currency     CHAR(3)        NULL,
    priority     NVARCHAR(10)   NOT NULL,
    summary      NVARCHAR(1000) NOT NULL,
    status       NVARCHAR(20)   NOT NULL,
    reviewer_id  NVARCHAR(40)   NOT NULL,
    created_at   DATETIME2(0)   NOT NULL
);
GO

-- Cuentas del cliente autenticado
CREATE OR ALTER PROCEDURE dbo.usp_CustomerAccounts
    @CustomerId NVARCHAR(10)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT account_id, account_type, currency, balance, status
    FROM dbo.accounts
    WHERE customer_id = @CustomerId
    ORDER BY account_id;
END
GO

-- Movimientos recientes: la pertenencia de la cuenta se valida AQUÍ (una cuenta ajena devuelve vacío)
CREATE OR ALTER PROCEDURE dbo.usp_RecentTransactions
    @CustomerId    NVARCHAR(10),
    @AccountSuffix NVARCHAR(4),
    @Limit         INT
AS
BEGIN
    SET NOCOUNT ON;
    IF @Limit < 1 OR @Limit > 20 SET @Limit = 5;
    SELECT TOP (@Limit)
        CONVERT(VARCHAR(16), t.posted_at, 120) AS posted_at,
        t.description, t.amount, t.currency, t.channel, t.merchant
    FROM dbo.transactions AS t
    JOIN dbo.accounts AS a ON a.account_id = t.account_id
    WHERE a.customer_id = @CustomerId
      AND RIGHT(a.account_id, 4) = @AccountSuffix
    ORDER BY t.posted_at DESC;
END
GO

-- Registro de un reclamo aprobado por el operador (único INSERT que puede hacer la aplicación)
CREATE OR ALTER PROCEDURE dbo.usp_RegisterComplaint
    @ComplaintId NVARCHAR(20), @CustomerId NVARCHAR(10), @ThreadId NVARCHAR(64),
    @Category NVARCHAR(30), @Product NVARCHAR(40), @Amount DECIMAL(18,2), @Currency CHAR(3),
    @Priority NVARCHAR(10), @Summary NVARCHAR(1000), @Status NVARCHAR(20), @ReviewerId NVARCHAR(40)
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.complaints (complaint_id, customer_id, thread_id, category, product, amount, currency,
                                priority, summary, status, reviewer_id, created_at)
    VALUES (@ComplaintId, @CustomerId, @ThreadId, @Category, @Product, @Amount, @Currency,
            @Priority, @Summary, @Status, @ReviewerId, SYSUTCDATETIME());
END
GO

-- Rol de aplicación: solo EXECUTE sobre los procedimientos, ningún permiso sobre tablas
IF DATABASE_PRINCIPAL_ID('app_banking') IS NULL CREATE ROLE app_banking;
GO
GRANT EXECUTE ON dbo.usp_CustomerAccounts TO app_banking;
GRANT EXECUTE ON dbo.usp_RecentTransactions TO app_banking;
GRANT EXECUTE ON dbo.usp_RegisterComplaint TO app_banking;
GO
