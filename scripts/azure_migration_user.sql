-- Run on Azure MS SQL Server as sysadmin
-- Replace <migration_password> and <your_database> with actual values

USE master;
GO

CREATE LOGIN migration_user
    WITH PASSWORD = '<migration_password>';
GO

USE [<your_database>];
GO

CREATE USER migration_user FOR LOGIN migration_user;
GO

ALTER ROLE db_datareader ADD MEMBER migration_user;
GO

GRANT VIEW DEFINITION TO migration_user;
GO
