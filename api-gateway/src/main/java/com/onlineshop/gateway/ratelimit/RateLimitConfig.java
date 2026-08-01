package com.onlineshop.gateway.ratelimit;

import io.github.bucket4j.distributed.proxy.ProxyManager;
import io.github.bucket4j.redis.lettuce.cas.LettuceBasedProxyManager;
import io.lettuce.core.ClientOptions;
import io.lettuce.core.RedisClient;
import io.lettuce.core.RedisURI;
import io.lettuce.core.SocketOptions;
import io.lettuce.core.api.StatefulRedisConnection;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Lazy;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;

import java.time.Duration;

@Configuration
@ConditionalOnProperty(name = "gateway.ratelimit.enabled", havingValue = "true", matchIfMissing = true)
public class RateLimitConfig {

    @Value("${spring.data.redis.timeout:100ms}")
    private Duration redisTimeout;

    @Value("${spring.data.redis.connect-timeout:10s}")
    private Duration redisConnectTimeout;

    private RedisClient redisClient;

    @Bean
    @Lazy
    public ProxyManager<String> bucket4jProxyManager(RedisConnectionFactory redisConnectionFactory) {
        if (!(redisConnectionFactory instanceof LettuceConnectionFactory lettuceFactory)) {
            throw new IllegalStateException(
                    "Rate limiting requires a LettuceConnectionFactory. Got: " +
                    redisConnectionFactory.getClass().getName());
        }

        RedisURI redisUri = RedisURI.builder()
                .withHost(lettuceFactory.getHostName())
                .withPort(lettuceFactory.getPort())
                .withTimeout(redisTimeout)
                .build();

        redisClient = RedisClient.create(redisUri);
        redisClient.setOptions(ClientOptions.builder()
                .socketOptions(SocketOptions.builder()
                        .connectTimeout(redisConnectTimeout)
                        .build())
                .build());

        StatefulRedisConnection<String, byte[]> connection = redisClient.connect(
                io.lettuce.core.codec.RedisCodec.of(
                        io.lettuce.core.codec.StringCodec.UTF8,
                        io.lettuce.core.codec.ByteArrayCodec.INSTANCE
                )
        );

        return LettuceBasedProxyManager.builderFor(connection)
                .build();
    }

    @PreDestroy
    public void shutdown() {
        if (redisClient != null) {
            redisClient.shutdown();
        }
    }
}
