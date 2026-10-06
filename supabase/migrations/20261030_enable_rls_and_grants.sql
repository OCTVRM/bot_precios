-- Migración para Supabase Data API: RLS y Grants explícitos (Requisito Octubre 30)
-- 1. Otorgar permisos de esquema
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

-- 2. Grants para productos e historial de precios (lectura pública y acceso total a service_role)
GRANT SELECT ON TABLE public.products TO anon, authenticated;
GRANT ALL ON TABLE public.products TO service_role;

GRANT SELECT ON TABLE public.price_history TO anon, authenticated;
GRANT ALL ON TABLE public.price_history TO service_role;

-- 3. Grants para suscripciones (solo service_role y postgres, sin acceso a anon/authenticated por privacidad)
REVOKE ALL ON TABLE public.subscriptions FROM anon, authenticated;
GRANT ALL ON TABLE public.subscriptions TO service_role;

-- 4. Permisos sobre secuencias
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- 5. Privilegios por defecto para futuras tablas y secuencias creadas en public
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO service_role;

-- 6. Activar Row Level Security (RLS) en todas las tablas
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.price_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

-- 7. Políticas de lectura pública para products y price_history, y bloqueo total para subscriptions
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'products' AND policyname = 'Allow public read access on products'
    ) THEN
        CREATE POLICY "Allow public read access on products"
        ON public.products
        FOR SELECT
        TO anon, authenticated
        USING (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'price_history' AND policyname = 'Allow public read access on price_history'
    ) THEN
        CREATE POLICY "Allow public read access on price_history"
        ON public.price_history
        FOR SELECT
        TO anon, authenticated
        USING (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'subscriptions' AND policyname = 'Deny public access to subscriptions'
    ) THEN
        CREATE POLICY "Deny public access to subscriptions"
        ON public.subscriptions
        FOR ALL
        TO anon, authenticated
        USING (false)
        WITH CHECK (false);
    END IF;
END $$;
