CREATE SCHEMA IF NOT EXISTS enterprise_data;


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