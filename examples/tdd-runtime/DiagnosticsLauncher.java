import com.example.shop.ShopApplication;
import com.github.dockerjava.api.model.ExposedPort;
import com.github.dockerjava.api.model.PortBinding;
import com.github.dockerjava.api.model.Ports;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.context.metrics.buffering.BufferingApplicationStartup;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.beans.factory.NoSuchBeanDefinitionException;
import org.testcontainers.containers.PostgreSQLContainer;

import java.util.concurrent.atomic.AtomicReference;

/** Explicit, disposable launcher: does not change ShopApplication or its security chain. */
public final class DiagnosticsLauncher {
    public static void main(String[] args) {
        PostgreSQLContainer<?> database = new PostgreSQLContainer<>("postgres:16-alpine")
                .withLabel("spring-runtime-mcp.validation", "tdd")
                .withCreateContainerCmdModifier(command -> command.getHostConfig().withPortBindings(
                        new PortBinding(Ports.Binding.bindIp("127.0.0.1"), new ExposedPort(5432))));
        AtomicReference<ConfigurableApplicationContext> context = new AtomicReference<>();
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            ConfigurableApplicationContext running = context.get();
            if (running != null) running.close();
            database.stop();
        }));
        try {
            database.start();
            SpringApplication application = new SpringApplication(ShopApplication.class);
            application.setApplicationStartup(new BufferingApplicationStartup(4096));
            ConfigurableApplicationContext running = application.run(
                    "--spring.config.additional-location=file:" + args[0],
                    "--spring.profiles.active=runtime-diagnostics",
                    "--management.server.port=0",
                    "--spring.datasource.url=" + database.getJdbcUrl(),
                    "--spring.datasource.username=" + database.getUsername(),
                    "--spring.datasource.password=" + database.getPassword(),
                    "--logging.level.root=OFF");
            context.set(running);
            int port = running.getEnvironment().getRequiredProperty("local.management.port", Integer.class);
            System.out.println("MCP_DIAGNOSTICS_READY=" + port);
            System.out.flush();
        } catch (Throwable failure) {
            // Never print target configuration, connection strings, secrets or stacks.
            Throwable cause = failure;
            for (int depth = 0; depth < 12 && cause.getCause() != null; depth++) cause = cause.getCause();
            System.err.println("MCP_DIAGNOSTICS_FAILED=" + cause.getClass().getName());
            if (cause instanceof NoSuchBeanDefinitionException missing && missing.getBeanType() != null) {
                System.err.println("MCP_DIAGNOSTICS_MISSING=" + missing.getBeanType().getName());
            }
            System.exit(1);
        }
    }
}
