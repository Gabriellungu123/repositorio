CREATE TABLE IF NOT EXISTS usuarios_general (
    id INT AUTO_INCREMENT PRIMARY KEY,
    usuario VARCHAR(100) NOT NULL,
    nombre_completo VARCHAR(200),
    departamento VARCHAR(100)
);

INSERT INTO usuarios_general (usuario, nombre_completo, departamento)
VALUES
('juanp', 'Juan Pérez', 'Ventas'),
('mariaf', 'María Fernández', 'Marketing'),
('carloss', 'Carlos Sánchez', 'Informática');

CREATE TABLE IF NOT EXISTS usuarios_informatica (
    id INT AUTO_INCREMENT PRIMARY KEY,
    usuario VARCHAR(100) NOT NULL,
    password VARCHAR(200) NOT NULL
);

INSERT INTO usuarios_informatica (usuario, password)
VALUES 
('admin', 'admin'),
('tecnico1', '1234'),
('tecnico2', '1234');

CREATE TABLE IF NOT EXISTS grupos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL
);

INSERT INTO grupos (nombre)
VALUES ('Soporte Técnico');

CREATE TABLE IF NOT EXISTS incidencias (
    id INT AUTO_INCREMENT PRIMARY KEY,
    titulo VARCHAR(200) NOT NULL,
    descripcion TEXT NOT NULL,
    estado ENUM('abierta', 'en_proceso', 'cerrada') DEFAULT 'abierta',
    prioridad ENUM('baja', 'media', 'alta') DEFAULT 'media',

    creador_id INT NULL,
    asignado_id INT NULL,
    grupo_id INT NULL,

    fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    FOREIGN KEY (creador_id) REFERENCES usuarios_general(id) ON DELETE SET NULL,
    FOREIGN KEY (asignado_id) REFERENCES usuarios_general(id) ON DELETE SET NULL,
    FOREIGN KEY (grupo_id) REFERENCES grupos(id) ON DELETE SET NULL
);

INSERT INTO incidencias (
    titulo, descripcion, estado, prioridad,
    creador_id, asignado_id, grupo_id
)
VALUES (
    'No funciona el correo',
    'El usuario no puede enviar ni recibir correos desde Outlook.',
    'abierta',
    'alta',
    1,   -- creador: Juan Pérez
    3,   -- asignado: tecnico2
    1    -- grupo: Soporte Técnico
);
CREATE TABLE IF NOT EXISTS usuarios_grupo (
    id INT AUTO_INCREMENT PRIMARY KEY,
    usuario_id INT NOT NULL,
    grupo_id INT NOT NULL,
    FOREIGN KEY (usuario_id) REFERENCES usuarios_informatica(id),
    FOREIGN KEY (grupo_id) REFERENCES grupos(id)
);

INSERT INTO usuarios_grupo (usuario_id, grupo_id)
VALUES
(1, 1),  -- admin → Soporte Técnico
(2, 1),  -- tecnico1 → Soporte Técnico
(3, 1);  -- tecnico2 → Soporte Técnico