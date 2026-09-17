materials.portability
=====================

See :ref:`material_discovery_queries` for live-state capture, supported types
and declared-domain limits. Payloads use native JSON and never resolve a
material from a file path or catalog name during restoration.

.. automodule:: optiland.materials.portability
   :members:

File-backed capture helpers
---------------------------

.. automethod:: optiland.materials.material_file.MaterialFile.from_data

.. automethod:: optiland.materials.material_file.MaterialFile.to_owned_material
