CREATE SCHEMA IF NOT EXISTS enterprise_data;


-- Idempotent: drop children first so the script can re-seed an existing DB.
DROP TABLE IF EXISTS enterprise_data.product_inventory;
DROP TABLE IF EXISTS enterprise_data.product_prices;
DROP TABLE IF EXISTS enterprise_data.warehouses;
DROP TABLE IF EXISTS enterprise_data.products;
DROP TABLE IF EXISTS enterprise_data.suppliers;


-- =============================================
-- CREATE SUPPLIERS
-- =============================================

CREATE TABLE enterprise_data.suppliers (
    supplier_id BIGINT PRIMARY KEY,
    supplier_name VARCHAR(500) NOT NULL
);

-- =============================================
-- CREATE PRODUCTS
-- =============================================

CREATE TABLE enterprise_data.products (
    product_id BIGINT PRIMARY KEY,
    product_name VARCHAR(500) NOT NULL,
    supplier_id BIGINT NOT NULL,
    part_number VARCHAR(100),
    language VARCHAR(50),
    country VARCHAR(100),

    CONSTRAINT fk_supplier
        FOREIGN KEY (supplier_id)
        REFERENCES enterprise_data.suppliers(supplier_id)
);

-- =============================================
-- CREATE PRODUCT PRICES
-- =============================================

CREATE TABLE enterprise_data.product_prices (
    price_id BIGINT PRIMARY KEY,
    product_id BIGINT NOT NULL,
    unit_price NUMERIC(12,2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    valid_from DATE NOT NULL,

    CONSTRAINT fk_price_product
        FOREIGN KEY (product_id)
        REFERENCES enterprise_data.products(product_id)
);

-- =============================================
-- CREATE WAREHOUSES
-- =============================================

CREATE TABLE enterprise_data.warehouses (
    warehouse_id BIGINT PRIMARY KEY,
    warehouse_name VARCHAR(200) NOT NULL,
    country VARCHAR(100) NOT NULL
);

-- =============================================
-- CREATE PRODUCT INVENTORY
-- =============================================

CREATE TABLE enterprise_data.product_inventory (
    inventory_id BIGINT PRIMARY KEY,
    product_id BIGINT NOT NULL,
    warehouse_id BIGINT NOT NULL,
    quantity INTEGER NOT NULL,
    last_updated DATE NOT NULL,

    CONSTRAINT fk_inventory_product
        FOREIGN KEY (product_id)
        REFERENCES enterprise_data.products(product_id),
    CONSTRAINT fk_inventory_warehouse
        FOREIGN KEY (warehouse_id)
        REFERENCES enterprise_data.warehouses(warehouse_id)
);


INSERT INTO enterprise_data.suppliers VALUES
(555,'Fisher Scientific'),
(556,'Sigma-Aldrich'),
(557,'Merck'),
(558,'Thermo Fisher Scientific'),
(559,'Avantor'),
(560,'Honeywell Research Chemicals'),
(561,'Alfa Aesar'),
(562,'TCI Chemicals'),
(563,'VWR Chemicals'),
(564,'Spectrum Chemical');


INSERT INTO enterprise_data.products
(product_id,product_name,supplier_id,part_number,language,country)
VALUES

(3731598,'4-Bromobutyryl Chloride, 95%',555,'303440250','English','UNITED KINGDOM'),

(3731599,'Acetone',555,'A18-4','English','UNITED STATES'),

(3731600,'Methanol',556,'34860','English','UNITED STATES'),

(3731601,'Ethanol Absolute',557,'100983','English','GERMANY'),

(3731602,'Isopropyl Alcohol',558,'A416-4','English','UNITED STATES'),

(3731603,'Hydrochloric Acid',559,'320331','English','UNITED STATES'),

(3731604,'Sulfuric Acid',557,'100731','English','GERMANY'),

(3731605,'Nitric Acid',555,'A200','English','UNITED STATES'),

(3731606,'Sodium Hydroxide',557,'106498','English','GERMANY'),

(3731607,'Potassium Hydroxide',556,'306576','English','UNITED STATES'),

(3731608,'Hydrogen Peroxide',555,'H325','English','UNITED STATES'),

(3731609,'Toluene',557,'108325','English','GERMANY'),

(3731610,'Xylene',556,'534056','English','UNITED STATES'),

(3731611,'Formaldehyde Solution',557,'104003','English','GERMANY'),

(3731612,'Ammonium Hydroxide',559,'221228','English','UNITED STATES');


INSERT INTO enterprise_data.product_prices
(price_id,product_id,unit_price,currency,valid_from)
VALUES
(9001,3731598,182.50,'GBP','2026-01-01'),
(9002,3731599,24.90,'USD','2026-01-01'),
(9003,3731600,18.75,'USD','2026-01-01'),
(9004,3731601,31.40,'EUR','2026-01-01'),
(9005,3731602,22.10,'USD','2026-01-01'),
(9006,3731603,15.60,'USD','2026-01-01'),
(9007,3731604,27.85,'EUR','2026-01-01'),
(9008,3731605,29.30,'USD','2026-01-01'),
(9009,3731606,12.45,'EUR','2026-01-01'),
(9010,3731607,19.95,'USD','2026-01-01'),
(9011,3731608,16.20,'USD','2026-01-01'),
(9012,3731609,21.70,'EUR','2026-01-01'),
(9013,3731610,23.55,'USD','2026-01-01'),
(9014,3731611,14.80,'EUR','2026-01-01'),
-- second price version for Acetone (newer, shows price history)
(9015,3731599,26.50,'USD','2026-06-01');
-- note: product 3731612 (Ammonium Hydroxide) intentionally has no price row


INSERT INTO enterprise_data.warehouses
(warehouse_id,warehouse_name,country)
VALUES
(41,'Frankfurt Central','GERMANY'),
(42,'Newark Distribution','UNITED STATES'),
(43,'Manchester North','UNITED KINGDOM');


-- note: products 3731605 (Nitric Acid) and 3731612 intentionally have no stock
INSERT INTO enterprise_data.product_inventory
(inventory_id,product_id,warehouse_id,quantity,last_updated)
VALUES
(7001,3731598,43,120,'2026-07-01'),
(7002,3731599,42,3400,'2026-07-01'),
(7003,3731599,41,750,'2026-07-01'),
(7004,3731600,42,2100,'2026-07-01'),
(7005,3731601,41,1800,'2026-07-01'),
(7006,3731602,42,950,'2026-07-01'),
(7007,3731603,42,640,'2026-07-01'),
(7008,3731604,41,880,'2026-07-01'),
(7009,3731606,41,1500,'2026-07-01'),
(7010,3731607,42,430,'2026-07-01'),
(7011,3731608,42,2750,'2026-07-01'),
(7012,3731609,41,690,'2026-07-01'),
(7013,3731610,42,510,'2026-07-01'),
(7014,3731611,41,320,'2026-07-01'),
(7015,3731601,43,260,'2026-07-01'),
(7016,3731608,43,140,'2026-07-01');
