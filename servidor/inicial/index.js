// Worker mínimo que reserva el nombre mientras se activa la primera versión aprobada del servidor.
export default {
  async fetch() {
    return new Response(JSON.stringify({ nombre: "MCP de Mapas DGA", estado: "en preparación" }), {
      status: 503,
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  },
};
